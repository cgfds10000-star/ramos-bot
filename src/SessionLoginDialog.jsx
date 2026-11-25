import React, {useEffect, useRef} from 'react';
import {X} from 'lucide-react';

export default function SessionLoginDialog({children, active, onClose}) {
  const dialog = useRef(null);
  const returnFocus = useRef(null);

  useEffect(() => {
    returnFocus.current = document.activeElement;
    return () => { if (returnFocus.current?.isConnected) returnFocus.current.focus({preventScroll: true}); };
  }, []);

  useEffect(() => {
    if (active && !dialog.current?.contains(document.activeElement)) {
      (dialog.current?.querySelector('[aria-selected="true"]') || dialog.current)?.focus({preventScroll: true});
    }
  }, [active]);

  function handleKey(event) {
    if (!active) return;
    if (event.key === 'Escape') { event.preventDefault(); onClose(); }
    if (event.key !== 'Tab') return;
    const focusable = [...dialog.current.querySelectorAll('button,input,select,a[href],[tabindex="0"]')]
      .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (!first) { event.preventDefault(); dialog.current.focus(); }
    else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  return <div className="session-login-backdrop" onClick={event => { if (active && event.target === event.currentTarget) onClose(); }} onKeyDown={handleKey}>
    <section className="session-login-popup" ref={dialog} role="dialog" aria-modal={active ? true : undefined} aria-label="Login do Instagram" tabIndex={-1} inert={!active}>
      <button type="button" className="session-popup-dismiss" aria-label="Fechar login" onClick={onClose}><X size={18}/></button>
      {children}
    </section>
  </div>;
}
