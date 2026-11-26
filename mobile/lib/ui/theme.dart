import 'dart:math' as math;
import 'package:flutter/material.dart';

const purple = Color(0xFFAF16EF);
const ink = Color(0xFF0C0914);
const muted = Color(0xFFB8AECF);

ThemeData ramosTheme() => ThemeData(
  brightness: Brightness.dark,
  fontFamily: 'Ramos',
  scaffoldBackgroundColor: ink,
  colorScheme: ColorScheme.fromSeed(
    seedColor: purple,
    brightness: Brightness.dark,
    surface: const Color(0xFF171021),
  ),
  useMaterial3: true,
  inputDecorationTheme: InputDecorationTheme(
    filled: true,
    fillColor: const Color(0xFF14101F),
    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(16),
      borderSide: const BorderSide(color: Color(0xFF56416D)),
    ),
    enabledBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(16),
      borderSide: const BorderSide(color: Color(0xFF56416D)),
    ),
    focusedBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(16),
      borderSide: const BorderSide(color: purple, width: 1.5),
    ),
    labelStyle: const TextStyle(color: muted),
  ),
  snackBarTheme: const SnackBarThemeData(backgroundColor: Color(0xFF43205D)),
  dialogTheme: DialogThemeData(
    backgroundColor: const Color(0xFF130D1E),
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(26)),
  ),
);

class RamosTexture extends StatelessWidget {
  final Widget child;
  const RamosTexture({super.key, required this.child});
  @override
  Widget build(BuildContext context) => DecoratedBox(
    decoration: const BoxDecoration(
      gradient: LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [Color(0xFF1A092C), ink, Color(0xFF12091C)],
      ),
    ),
    child: CustomPaint(painter: _TexturePainter(), child: child),
  );
}

class _TexturePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = const Color(0xFF9451B5).withValues(alpha: .045)
      ..strokeWidth = .6;
    for (double x = -size.height; x < size.width; x += 7) {
      canvas.drawLine(
        Offset(x, 0),
        Offset(x + size.height, size.height),
        paint,
      );
    }
    final glow = Paint()
      ..shader =
          const RadialGradient(
            colors: [Color(0x448100B8), Color(0x008100B8)],
          ).createShader(
            Rect.fromCircle(
              center: Offset(size.width, 0),
              radius: math.max(size.width, 300),
            ),
          );
    canvas.drawRect(Offset.zero & size, glow);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class RamosBrand extends StatelessWidget {
  final double size;
  const RamosBrand({super.key, this.size = 25});
  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      SizedBox(
        width: size + 5,
        height: size + 5,
        child: CustomPaint(painter: _RamosMark()),
      ),
      RichText(
        text: TextSpan(
          style: TextStyle(
            fontFamily: 'Ramos',
            fontSize: size,
            fontWeight: FontWeight.w700,
            letterSpacing: 1,
          ),
          children: const [
            TextSpan(text: 'A'),
            TextSpan(
              text: 'M',
              style: TextStyle(color: Color(0xFF00C6E8)),
            ),
            TextSpan(text: 'OS BOT'),
          ],
        ),
      ),
    ],
  );
}

class _RamosMark extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / 64, size.height / 64);
    final path = Path()
      ..moveTo(18, 48)
      ..lineTo(18, 16)
      ..lineTo(37, 16)
      ..cubicTo(56, 16, 56, 41, 39, 41)
      ..lineTo(49, 48)
      ..lineTo(35, 48)
      ..lineTo(23, 34)
      ..lineTo(36, 34)
      ..cubicTo(43, 34, 43, 26, 36, 26)
      ..lineTo(28, 26)
      ..lineTo(28, 48)
      ..close();
    canvas.drawPath(path, Paint()..color = const Color(0xFFB72CF4));
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class GlowButton extends StatelessWidget {
  final String label;
  final VoidCallback? onPressed;
  final bool busy;
  final IconData? icon;
  const GlowButton(
    this.label, {
    super.key,
    this.onPressed,
    this.busy = false,
    this.icon,
  });
  @override
  Widget build(BuildContext context) => Opacity(
    opacity: onPressed == null && !busy ? .45 : 1,
    child: DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(17),
        border: Border.all(
          color: const Color(0xFFE468FA).withValues(alpha: .7),
        ),
        gradient: const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFFC01CF0), Color(0xFF7800BA)],
        ),
        boxShadow: [
          BoxShadow(
            color: purple.withValues(alpha: .14),
            blurRadius: 18,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: busy ? null : onPressed,
          borderRadius: BorderRadius.circular(17),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 17),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                if (busy)
                  const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                else if (icon != null)
                  Icon(icon, size: 21),
                if (busy || icon != null) const SizedBox(width: 10),
                Flexible(
                  child: Text(
                    label,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    ),
  );
}

class MessageBox extends StatelessWidget {
  final String text;
  final bool error;
  const MessageBox(this.text, {super.key, this.error = false});
  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    margin: const EdgeInsets.symmetric(vertical: 10),
    padding: const EdgeInsets.all(13),
    decoration: BoxDecoration(
      color: (error ? Colors.red : purple).withValues(alpha: .10),
      borderRadius: BorderRadius.circular(14),
      border: Border.all(
        color: (error ? Colors.redAccent : purple).withValues(alpha: .35),
      ),
    ),
    child: Text(
      text,
      style: TextStyle(
        color: error ? const Color(0xFFFFB7C7) : muted,
        height: 1.4,
      ),
    ),
  );
}
