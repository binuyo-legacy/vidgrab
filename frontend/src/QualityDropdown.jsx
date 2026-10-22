import { useEffect, useRef, useState } from "react";
import "./quality-dropdown.css";

const CLOSE_MS = 160;

// Custom listbox replacing the native <select>: same values, but styled and animated to match VibeN.
export default function QualityDropdown({ options, value, onChange, disabled, title, dim }) {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const wrapRef = useRef(null);
  const triggerRef = useRef(null);
  const optionRefs = useRef([]);
  const closeTimer = useRef(null);

  const selectedIndex = Math.max(0, options.findIndex((o) => o.value === value));
  const selected = options[selectedIndex];
  const showPanel = open || closing;

  function openPanel() {
    clearTimeout(closeTimer.current);
    setClosing(false);
    setOpen(true);
  }

  function closePanel({ restoreFocus } = {}) {
    if (!open) return;
    setOpen(false);
    setClosing(true);
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setClosing(false), CLOSE_MS);
    if (restoreFocus) triggerRef.current?.focus();
  }

  // Focus the selected option when the panel opens so keyboard users land on it.
  useEffect(() => {
    if (open) {
      const t = setTimeout(() => optionRefs.current[selectedIndex]?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [open, selectedIndex]);

  // Close on outside tap/click.
  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (!wrapRef.current?.contains(e.target)) closePanel();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown, { passive: true });
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, [open]);

  useEffect(() => () => clearTimeout(closeTimer.current), []);

  // If the control gets disabled while open (e.g. audio mode, or a download starts), close it.
  useEffect(() => {
    if (disabled && open) setOpen(false);
  }, [disabled, open]);

  function choose(i) {
    onChange(options[i].value);
    closePanel({ restoreFocus: true });
  }

  function onTriggerKey(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openPanel();
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      closePanel();
    }
  }

  function onOptionKey(e, i) {
    const last = options.length - 1;
    let next = null;
    if (e.key === "ArrowDown") next = i >= last ? 0 : i + 1;
    else if (e.key === "ArrowUp") next = i <= 0 ? last : i - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    if (next !== null) {
      e.preventDefault();
      optionRefs.current[next]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closePanel({ restoreFocus: true });
    } else if (e.key === "Tab") {
      closePanel();
    }
  }

  return (
    <div
      className={`qd-wrap${dim ? " dim" : ""}`}
      ref={wrapRef}
      onBlur={(e) => {
        if (open && !wrapRef.current?.contains(e.relatedTarget)) closePanel();
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className={`qd-trigger${open ? " is-open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="quality"
        disabled={disabled}
        title={title}
        onClick={() => (open ? closePanel() : openPanel())}
        onKeyDown={onTriggerKey}
      >
        <span className="qd-value">{selected.label}</span>
        <svg className="qd-chevron" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {showPanel && (
        <div
          className={`qd-panel${closing ? " is-closing" : ""}`}
          role="listbox"
          aria-label="quality"
        >
          {options.map((o, i) => {
            const isSel = o.value === value;
            return (
              <button
                key={o.value}
                ref={(el) => (optionRefs.current[i] = el)}
                type="button"
                role="option"
                aria-selected={isSel}
                tabIndex={open ? 0 : -1}
                className={`qd-option${isSel ? " is-selected" : ""}`}
                style={{ animationDelay: `${i * 22}ms` }}
                onClick={() => choose(i)}
                onKeyDown={(e) => onOptionKey(e, i)}
              >
                <span className="qd-radio" aria-hidden="true" />
                <span className="qd-label">{o.label}</span>
                {isSel && (
                  <svg className="qd-check" viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M3.5 8.5 6.5 11.5 12.5 4.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
