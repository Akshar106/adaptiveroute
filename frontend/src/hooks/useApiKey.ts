import { useCallback, useState } from "react";

const STORAGE_KEY = "adaptiveroute.apiKey";

// localStorage can throw (private mode, blocked storage), so every access is guarded.
function readKey(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function useApiKey(): [string, (key: string) => void] {
  const [apiKey, setApiKey] = useState(readKey);
  const save = useCallback((key: string) => {
    setApiKey(key);
    try {
      if (key) localStorage.setItem(STORAGE_KEY, key);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      // still usable for this session
    }
  }, []);
  return [apiKey, save];
}
