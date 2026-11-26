package com.ramos.app.login

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.content.Intent
import android.net.Uri
import android.webkit.CookieManager
import android.app.Activity
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.util.UUID
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

class MainActivity : FlutterActivity() {
    private var appChannel: MethodChannel? = null
    private var pendingExport: MethodChannel.Result? = null
    private var exportText: String? = null
    private val alias = "ramos_login_usdid_v1"
    private fun b64(bytes: ByteArray) = Base64.encodeToString(bytes, Base64.NO_WRAP)
    private fun url(bytes: ByteArray) = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    private fun json(value: Map<String, Any>) = JSONObject(value).toString()
    private fun identity(): Map<String, String> {
        val prefs = getSharedPreferences("login_device", MODE_PRIVATE)
        fun id(key: String): String {
            val prior = prefs.getString(key, null)
            if (prior != null) return prior
            val value = if (key == "android") "android-" + UUID.randomUUID().toString().replace("-", "").take(16) else UUID.randomUUID().toString()
            prefs.edit().putString(key, value).apply()
            return value
        }
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!store.containsAlias(alias)) {
            prefs.edit().putBoolean("registered", false).apply()
            KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply {
                initialize(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256).build())
            }.generateKeyPair()
        }
        val publicDer = store.getCertificate(alias).publicKey.encoded
        val publicB64 = b64(publicDer)
        val kid = url(MessageDigest.getInstance("SHA-256").digest(publicB64.toByteArray()))
        fun sign(text: String): String = url(Signature.getInstance("SHA256withECDSA").run {
            initSign(store.getKey(alias, null) as java.security.PrivateKey)
            update(text.toByteArray()); sign()
        })
        val now = System.currentTimeMillis() / 1000
        val usdid = id("usdid")
        val payload = url(json(mapOf("sub" to usdid, "iat" to now, "aud" to "567067343352427", "exp" to now + 3600, "pub" to publicB64, "alg" to "ES256")).toByteArray())
        val protected = url(json(mapOf("typ" to "JWT", "alg" to "ES256", "kid" to kid, "aid" to "567067343352427", "ver" to "1")).toByteArray())
        val token = JSONObject().put("payload", payload).put("signatures", org.json.JSONArray().put(JSONObject().put("protected", protected).put("signature", sign("$protected.$payload"))))
        val part = "$usdid.${now + 3600}"
        return mapOf("registered" to prefs.getBoolean("registered", false).toString(), "uuid" to id("uuid"), "phone" to id("phone"), "android" to id("android"), "registration" to url(token.toString().toByteArray()), "usdidHeader" to "$part.${sign(part)}")
    }
    private fun encrypt(args: Map<*, *>): String {
        val password = args["password"] as String
        val encoded = args["publicKey"] as String
        val keyId = (args["keyId"] as String).toInt()
        require(keyId in 0..255)
        val pem = String(Base64.decode(encoded, Base64.DEFAULT))
        val der = if (pem.contains("BEGIN PUBLIC KEY")) Base64.decode(pem.replace("-----BEGIN PUBLIC KEY-----", "").replace("-----END PUBLIC KEY-----", "").replace(Regex("\\s"), ""), Base64.DEFAULT) else Base64.decode(encoded, Base64.DEFAULT)
        val rsa = KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(der))
        val random = SecureRandom()
        val key = ByteArray(32).also { random.nextBytes(it) }
        val iv = ByteArray(12).also { random.nextBytes(it) }
        val wrapped = Cipher.getInstance("RSA/ECB/PKCS1Padding").run { init(Cipher.ENCRYPT_MODE, rsa); doFinal(key) }
        val timestamp = (System.currentTimeMillis() / 1000).toString()
        val encrypted = Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, iv))
            updateAAD(timestamp.toByteArray()); doFinal(password.toByteArray(Charsets.UTF_8))
        }
        val size = ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort(wrapped.size.toShort()).array()
        val tag = encrypted.takeLast(16).toByteArray()
        val ciphertext = encrypted.dropLast(16).toByteArray()
        key.fill(0)
        return "#PWD_INSTAGRAM:4:$timestamp:" + b64(byteArrayOf(1, keyId.toByte()) + iv + size + wrapped + tag + ciphertext)
    }
    private fun stateKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val stateAlias = "ramos_login_state_v1"
        if (!store.containsAlias(stateAlias)) {
            KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore").apply {
                init(KeyGenParameterSpec.Builder(stateAlias,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256).build())
            }.generateKey()
        }
        return store.getKey(stateAlias,null) as SecretKey
    }
    private fun writeState(text: String) {
        val cipher=Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE,stateKey())
        val encrypted=cipher.doFinal(text.toByteArray(Charsets.UTF_8))
        getSharedPreferences("login_session",MODE_PRIVATE).edit().putString("encrypted",b64(cipher.iv+encrypted)).commit()
    }
    private fun readState(): String? {
        val value=getSharedPreferences("login_session",MODE_PRIVATE).getString("encrypted",null) ?: return null
        val bytes=Base64.decode(value,Base64.DEFAULT)
        require(bytes.size>28)
        val cipher=Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE,stateKey(),GCMParameterSpec(128,bytes.copyOfRange(0,12)))
        return String(cipher.doFinal(bytes.copyOfRange(12,bytes.size)),Charsets.UTF_8)
    }
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "ramos/login_crypto")
        appChannel = channel
        channel.setMethodCallHandler { call, result ->
            try {
                when (call.method) {
                    "identity" -> result.success(identity())
                    "readState" -> result.success(readState())
                    "writeState" -> { writeState(call.arguments as String); result.success(null) }
                    "clearState" -> { getSharedPreferences("login_session",MODE_PRIVATE).edit().remove("encrypted").commit(); result.success(null) }
                    "markRegistered" -> { getSharedPreferences("login_device", MODE_PRIVATE).edit().putBoolean("registered", true).apply(); result.success(null) }
                    "encryptPassword" -> result.success(encrypt(call.arguments as Map<*, *>))
                    "initialLink" -> result.success(intent?.dataString)
                    "setPanelCookie" -> {
                        val args = call.arguments as Map<*, *>
                        val url = args["url"] as String
                        val cookie = args["cookie"] as String
                        val uri = Uri.parse(url)
                        require(uri.scheme == "https" && !uri.host.isNullOrEmpty() && uri.userInfo == null)
                        require(cookie.startsWith("ramos_access=") && !cookie.contains(";") && !cookie.contains("\n") && !cookie.contains("\r"))
                        val manager = CookieManager.getInstance()
                        manager.setAcceptCookie(true)
                        manager.setCookie(url, "$cookie; Path=/; Secure; HttpOnly; SameSite=Strict") {
                            manager.flush(); result.success(null)
                        }
                    }
                    "exportText" -> {
                        if (pendingExport != null) { result.error("export_busy", "Conclua a exportacao anterior.", null) }
                        else {
                            val args = call.arguments as Map<*, *>
                            exportText = args["text"] as String
                            require(exportText!!.length <= 4 * 1024 * 1024)
                            val filename = (args["name"] as? String ?: "ramos-logs.txt").replace(Regex("[^a-zA-Z0-9._-]"), "_")
                            pendingExport = result
                            startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                                addCategory(Intent.CATEGORY_OPENABLE); type = "text/plain"; putExtra(Intent.EXTRA_TITLE, filename)
                            }, 7210)
                        }
                    }
                    else -> result.notImplemented()
                }
            } catch (_: Exception) { result.error("crypto_error", "Nao foi possivel preparar a criptografia do login.", null) }
        }
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.dataString?.let { appChannel?.invokeMethod("openSession", it) }
    }
    @Deprecated("Android activity result compatibility")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != 7210) return
        val result = pendingExport
        try {
            val uri = data?.data
            if (resultCode == Activity.RESULT_OK && uri != null) {
                contentResolver.openOutputStream(uri)?.use { it.write((exportText ?: "").toByteArray(Charsets.UTF_8)) }
                result?.success(true)
            } else result?.success(false)
        } catch (_: Exception) { result?.error("export_error", "Nao foi possivel salvar o arquivo.", null) }
        finally { pendingExport = null; exportText = null }
    }
}
