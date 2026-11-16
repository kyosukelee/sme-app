// 入力が落ち着いたら、数量と単価が正しい行だけをサーバーで計算する
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { isCalculable, toLine, type LiveCalc, type Row } from "./lines";

export function useLiveCalc(
  tenantId: string,
  rows: Row[],
  onDate: string | null,
  initial: LiveCalc | null,
  onError: (message: string) => void,
  withholding = false, // 源泉徴収税額も計算する(請求書)
) {
  const [calc, setCalc] = useState<LiveCalc | null>(initial);
  const timer = useRef<number>(undefined);
  const errorRef = useRef(onError);
  errorRef.current = onError;

  useEffect(() => {
    const targets = rows.filter(isCalculable);
    window.clearTimeout(timer.current);
    const controller = new AbortController();
    timer.current = window.setTimeout(() => {
      api
        .calculate(tenantId, targets.map(toLine), onDate, controller.signal, withholding)
        .then((result) => setCalc({ result, keys: targets.map((r) => r.key) }))
        .catch((e: Error) => e.name !== "AbortError" && errorRef.current(e.message));
    }, 300);
    return () => controller.abort();
  }, [rows, tenantId, onDate, withholding]);

  const amountByKey = useMemo(() => {
    const map = new Map<number, string>();
    calc?.keys.forEach((key, i) => map.set(key, calc.result.lines[i].amount));
    return map;
  }, [calc]);

  return { calc, setCalc, amountByKey };
}
