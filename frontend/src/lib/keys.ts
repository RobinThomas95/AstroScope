/** User-supplied API keys, kept only in this browser (localStorage) and sent as headers. */

export interface UserKeys {
  adsToken: string;
  geminiKey: string;
  geminiModel: string;
}

const STORAGE = "astroscope.keys";
const listeners = new Set<() => void>();

export function loadKeys(): UserKeys {
  try {
    const raw = localStorage.getItem(STORAGE);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<UserKeys>;
      return { adsToken: parsed.adsToken ?? "", geminiKey: parsed.geminiKey ?? "", geminiModel: parsed.geminiModel ?? "" };
    }
  } catch {
    /* storage unavailable */
  }
  return { adsToken: "", geminiKey: "", geminiModel: "" };
}

export function saveKeys(keys: UserKeys): void {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(keys));
  } catch {
    /* storage unavailable */
  }
  for (const l of listeners) l();
}

export function onKeysChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Headers carrying the user's keys (empty when none are set: the server default applies). */
export function keyHeaders(): Record<string, string> {
  const k = loadKeys();
  const h: Record<string, string> = {};
  if (k.adsToken) h["X-ADS-Token"] = k.adsToken;
  if (k.geminiKey) h["X-Gemini-Key"] = k.geminiKey;
  return h;
}
