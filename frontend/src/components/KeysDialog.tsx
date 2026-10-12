import { useEffect, useState } from "react";
import { ApiError, fetchKeyStatus, fetchLlmModels } from "../lib/api";
import { loadKeys, saveKeys, type UserKeys } from "../lib/keys";
import type { KeyStatus } from "../types";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function KeysDialog({ open, onClose }: Props) {
  const [keys, setKeys] = useState<UserKeys>(loadKeys());
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [models, setModels] = useState<string[] | null>(null);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setKeys(loadKeys());
    setMessage(null);
    fetchKeyStatus().then(setStatus).catch(() => setStatus(null));
  }, [open]);

  if (!open) return null;

  const save = () => {
    saveKeys({ ...keys, adsToken: keys.adsToken.trim(), geminiKey: keys.geminiKey.trim(), geminiModel: keys.geminiModel.trim() });
    setMessage("Saved in this browser.");
  };

  const testGemini = async () => {
    saveKeys({ ...keys, geminiKey: keys.geminiKey.trim() });
    setTesting(true);
    setMessage(null);
    try {
      const res = await fetchLlmModels();
      setModels(res.models);
      setMessage(
        `Gemini key accepted. ${res.models.length} text models available; "auto" will use ${res.suggested ?? "?"}.`,
      );
    } catch (e: unknown) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-[34rem] max-w-[95vw] rounded-lg border border-slate-700 bg-slate-900 p-4 text-sm shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold text-white">API keys</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white" aria-label="Close">
            ✕
          </button>
        </div>
        <p className="mb-3 text-xs text-slate-400">
          Keys are stored only in this browser and sent with your own requests. They are never
          saved on the server.
          {status && (status.ads_server_key || status.gemini_server_key) && (
            <>
              {" "}
              This server already provides a default{" "}
              {[status.ads_server_key && "ADS token", status.gemini_server_key && "Gemini key"].filter(Boolean).join(" and ")}
              ; your own key overrides it.
            </>
          )}
        </p>

        <label className="mb-1 block text-xs font-medium text-slate-300">
          NASA ADS token{" "}
          <a href="https://ui.adsabs.harvard.edu/user/settings/token" target="_blank" rel="noreferrer" className="text-sky-400">
            get one (free)
          </a>
        </label>
        <input
          type="password"
          value={keys.adsToken}
          onChange={(e) => setKeys({ ...keys, adsToken: e.target.value })}
          placeholder={status?.ads_server_key ? "optional: server default is set" : "paste your ADS API token"}
          className="mb-3 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100"
          autoComplete="off"
        />

        <label className="mb-1 block text-xs font-medium text-slate-300">
          Google Gemini API key{" "}
          <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="text-sky-400">
            get one (free tier)
          </a>
        </label>
        <div className="mb-2 flex gap-2">
          <input
            type="password"
            value={keys.geminiKey}
            onChange={(e) => setKeys({ ...keys, geminiKey: e.target.value })}
            placeholder={status?.gemini_server_key ? "optional: server default is set" : "paste your Gemini API key"}
            className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100"
            autoComplete="off"
          />
          <button type="button" onClick={testGemini} disabled={testing} className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-50">
            {testing ? "testing…" : "test"}
          </button>
        </div>
        <label className="mb-1 block text-xs font-medium text-slate-300">
          Gemini model <span className="font-normal text-slate-500">(leave empty or “auto” to pick the newest Flash model your key can use)</span>
        </label>
        <input
          list="gemini-models"
          value={keys.geminiModel}
          onChange={(e) => setKeys({ ...keys, geminiModel: e.target.value })}
          placeholder="auto"
          className="mb-3 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100"
        />
        <datalist id="gemini-models">{(models ?? []).map((m) => <option key={m} value={m} />)}</datalist>

        {message && <p className="mb-2 text-xs text-amber-300">{message}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded border border-slate-700 px-3 py-1 text-slate-300 hover:bg-slate-800">
            Close
          </button>
          <button type="button" onClick={save} className="rounded bg-sky-600 px-3 py-1 text-white hover:bg-sky-500">
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
