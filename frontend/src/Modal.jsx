import { useEffect, useRef, useState } from "react";

const EXIT_MS = 180;
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Animated glass dialog. Replaces native alert/confirm/prompt-style popups.
// Closes on backdrop click, Escape, or the close button. Keeps focus inside while open.
export default function Modal({ open, title, children, onClose, actions }) {
  const [mounted, setMounted] = useState(open);
  const [closing, setClosing] = useState(false);
  const panelRef = useRef(null);
  const lastFocus = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Keep the node mounted long enough to play the exit animation.
  useEffect(() => {
    if (open) {
      setMounted(true);
      setClosing(false);
      return undefined;
    }
    if (!mounted) return undefined;
    setClosing(true);
    const t = setTimeout(() => {
      setMounted(false);
      setClosing(false);
    }, EXIT_MS);
    return () => clearTimeout(t);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Focus management, Escape, and Tab trap while open.
  useEffect(() => {
    if (!open) return undefined;
    lastFocus.current = document.activeElement;
    const focusFirst = setTimeout(() => {
      const first = panelRef.current?.querySelector(FOCUSABLE) || panelRef.current;
      first?.focus();
    }, 30);

    function onKey(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current?.();
        return;
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      const nodes = [...panelRef.current.querySelectorAll(FOCUSABLE)];
      if (nodes.length === 0) {
        e.preventDefault();
        panelRef.current.focus();
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      clearTimeout(focusFirst);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      if (lastFocus.current && typeof lastFocus.current.focus === "function") {
        lastFocus.current.focus();
      }
    };
  }, [open]);

  if (!mounted) return null;

  return (
    <div
      className={`modal-root ${closing ? "is-closing" : "is-open"}`}
      onMouseDown={(e) => {
        // Only a press that starts on the backdrop closes it.
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        ref={panelRef}
        className="modal-panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        tabIndex={-1}
      >
        <div className="modal-head">
          <h3 id="modal-title" className="modal-title">{title}</h3>
          <button type="button" className="modal-x" aria-label="close" onClick={onClose}>
            <span aria-hidden="true">×</span>
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {actions && <div className="modal-actions">{actions}</div>}
      </div>
    </div>
  );
}
