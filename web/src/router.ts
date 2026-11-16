// URL の # 以降で画面を切り替える(例: #/quotes/new)
import { useEffect, useState } from "react";

export function useHashPath(): string {
  const read = () => window.location.hash.replace(/^#/, "") || "/quotes";
  const [path, setPath] = useState(read);
  useEffect(() => {
    const onChange = () => setPath(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return path;
}

export function navigate(path: string): void {
  window.location.hash = path;
}
