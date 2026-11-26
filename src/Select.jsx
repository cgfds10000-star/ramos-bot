import React, {Children, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {Check, ChevronDown} from 'lucide-react';

export default function Select({value, onChange, children, disabled = false, className = '', ...props}) {
  const id = useId();
  const trigger = useRef(null);
  const menu = useRef(null);
  const search = useRef({text: '', time: 0});
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState(null);
  const options = Children.toArray(children).filter(isValidElement).map(option => ({
    value: String(option.props.value), label: option.props.children, disabled: Boolean(option.props.disabled),
  }));
  const selected = options.findIndex(option => option.value === String(value));
  const enabled = options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0);
  const locked = disabled || !enabled.length;

  function show(last = false) {
    if (locked) return;
    setActive(selected >= 0 && !options[selected].disabled ? selected : enabled[last ? enabled.length - 1 : 0]);
    setPosition(null); setOpen(true);
  }

  function choose(index) {
    if (!options[index] || options[index].disabled) return;
    onChange(options[index].value);
    setOpen(false);
    trigger.current?.focus({preventScroll: true});
  }

  useEffect(() => { if (locked) setOpen(false); }, [locked]);
  useEffect(() => {
    if (!open) return;
    const outside = event => {
      if (!trigger.current?.contains(event.target) && !menu.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(Math.max(rect.width, 224), window.innerWidth - 16);
      const below = window.innerHeight - rect.bottom - 16;
      const above = rect.top - 16;
      const upward = below < Math.min(220, options.length * 48 + 14) && above > below;
      setPosition({width, left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        maxHeight: Math.max(48, Math.min(320, upward ? above : below)),
        ...(upward ? {bottom: window.innerHeight - rect.top + 8} : {top: rect.bottom + 8})});
    };
    place();
    window.addEventListener('resize', place);
    document.addEventListener('scroll', place, true);
    return () => {window.removeEventListener('resize', place); document.removeEventListener('scroll', place, true);};
  }, [open, options.length]);

  useEffect(() => {
    if (open) document.getElementById(`${id}-option-${active}`)?.scrollIntoView({block: 'nearest'});
  }, [open, active, id]);

  function keyDown(event) {
    if (locked) return;
    if (event.key === 'Tab') {setOpen(false); return;}
    if (event.key === 'Escape') {event.preventDefault(); setOpen(false); return;}
    if (['Enter', ' '].includes(event.key)) {
      event.preventDefault();
      if (open) choose(active); else show();
      return;
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (!open) {show(event.key === 'ArrowUp' || event.key === 'End'); return;}
      const index = enabled.indexOf(active);
      setActive(event.key === 'Home' ? enabled[0] : event.key === 'End' ? enabled.at(-1)
        : enabled[Math.max(0, Math.min(enabled.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      const fold = text => String(text).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
      search.current = {text: now - search.current.time < 600 ? search.current.text + event.key : event.key, time: now};
      const found = enabled.find(index => fold(options[index].label).startsWith(fold(search.current.text)));
      if (found !== undefined) {event.preventDefault(); if (!open) show(); setActive(found);}
    }
  }

  return <>
    <button {...props} ref={trigger} type="button" role="combobox" className={`ramos-select-trigger ${className}`}
      disabled={locked} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? id : undefined}
      aria-activedescendant={open && options[active] ? `${id}-option-${active}` : undefined}
      onClick={() => open ? setOpen(false) : show()} onKeyDown={keyDown}>
      <span>{options[selected]?.label || options[0]?.label || 'Selecione'}</span><ChevronDown className="ramos-select-arrow" aria-hidden="true"/>
    </button>
    {open && typeof document !== 'undefined' && createPortal(
      <div ref={menu} id={id} role="listbox" aria-label={props['aria-label']} className="ramos-select-menu"
        style={position || {visibility: 'hidden'}} onContextMenu={event => event.preventDefault()}>
        {options.map((option, index) => <div id={`${id}-option-${index}`} key={option.value} role="option"
          aria-selected={index === selected} aria-disabled={option.disabled || undefined}
          className={`ramos-select-option ${index === active ? 'is-active' : ''}`}
          onPointerMove={() => !option.disabled && setActive(index)} onMouseDown={event => event.preventDefault()}
          onClick={() => choose(index)}><span>{option.label}</span>{index === selected && <Check aria-hidden="true"/>}</div>)}
      </div>, document.body)}
  </>;
}
