import React, {useEffect, useRef} from 'react';
import {Ellipsis} from 'lucide-react';

export default function SessionTabs({sessions, selectedId, onSelect, onOptions}) {
  const list = useRef(null);
  const drag = useRef(null);
  const dragged = useRef(false);

  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({
      block: 'nearest', inline: 'nearest',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    });
  }, [selectedId, sessions.length]);

  function navigate(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !sessions.length) return;
    event.preventDefault();
    const index = Math.max(0, sessions.findIndex(item => item.id === selectedId));
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? sessions.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + sessions.length) % sessions.length;
    onSelect(sessions[next].id);
    list.current?.querySelectorAll('[role="tab"]')[next]?.focus({preventScroll: true});
  }

  function endDrag(event) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
  }

  return <div className="session-toolbar">
    <div className="session-tabs" ref={list} role="tablist" aria-label="Sessões" onKeyDown={navigate}
      onPointerDown={event => {
        dragged.current = false;
        if (event.pointerType === 'mouse' && event.button === 0) drag.current = {x: event.clientX, left: event.currentTarget.scrollLeft};
      }}
      onPointerMove={event => {
        if (!drag.current) return;
        const distance = event.clientX - drag.current.x;
        if (!dragged.current && Math.abs(distance) < 6) return;
        dragged.current = true;
        event.currentTarget.setPointerCapture(event.pointerId);
        event.currentTarget.scrollLeft = drag.current.left - distance;
        event.preventDefault();
      }}
      onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={() => { drag.current = null; }}
      onClickCapture={event => { if (dragged.current) { event.preventDefault(); event.stopPropagation(); dragged.current = false; } }}>
      {sessions.map(item => <button type="button" role="tab" id={`session-tab-${item.id}`} aria-controls="session-panel"
        aria-selected={item.id === selectedId} tabIndex={item.id === selectedId ? 0 : -1}
        className={item.id === selectedId ? 'active' : ''} key={item.id} onClick={() => onSelect(item.id)}>
        {item.name}{item.authenticated && <i aria-label="Conectada"/>}
      </button>)}
      {!sessions.length && <span className="empty-tab">Sessões</span>}
    </div>
    <button type="button" className="icon-button" aria-label="Opções da sessão" onClick={onOptions}><Ellipsis size={22}/></button>
  </div>;
}
