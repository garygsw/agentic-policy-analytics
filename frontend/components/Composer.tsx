"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";

interface Props {
  text: string;
  onText: (text: string) => void;
  placeholder: string;
  disabled: boolean;
  onSubmit: (text: string) => Promise<boolean>; // false: refused, so keep the text for editing
  left?: ReactNode; // the + menu (tools and settings)
  right?: ReactNode; // the model menu, beside the send button
  focusKey?: number; // changes when the page wants the box focused (a new conversation), so typing can start at once
}

/** The question box. Enter sends, Shift+Enter adds a line; typing with an input method (e.g. Chinese) is safe. */
export function Composer({ text, onText, placeholder, disabled, onSubmit, left, right, focusKey }: Props) {
  const [sending, setSending] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (focusKey) box.current?.focus(); // after the render, when the box is enabled again
  }, [focusKey]);

  const submit = async () => {
    const value = text.trim();
    if (!value || disabled || sending) return;
    setSending(true);
    const accepted = await onSubmit(value);
    setSending(false);
    if (accepted) onText("");
  };

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <textarea
        ref={box}
        aria-label="Your question"
        value={text}
        placeholder={placeholder}
        disabled={disabled}
        rows={2}
        onChange={(e) => onText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void submit();
          }
        }}
      />
      <div className="composer-foot">
        {left}
        <span className="spacer" />
        {right}
        <button type="submit" className="send" disabled={disabled || sending || !text.trim()} aria-label="Send">↑</button>
      </div>
    </form>
  );
}
