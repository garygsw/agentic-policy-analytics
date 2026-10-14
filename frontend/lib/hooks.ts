"use client";
import { useEffect, useState } from "react";

/** State remembered in this browser (settings such as the model or verbosity). Falls back to the default
 * when storage is unavailable or holds something unreadable. */
export function useStoredState<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(key);
      if (saved !== null) setValue(JSON.parse(saved) as T);
    } catch {
      // keep the default
    }
  }, [key]);
  const save = (next: T) => {
    setValue(next);
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // storage unavailable (e.g. private mode): the setting lasts until the page is closed
    }
  };
  return [value, save];
}

/** The current time in seconds, updated every second while `ticking` (for elapsed-time counters). */
export function useNow(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now() / 1000);
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}
