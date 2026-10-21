import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

export interface TipRow {
  value: string;
  label: string;
  color?: string;
  hollow?: boolean;
}

export interface Tip {
  x: number;
  y: number;
  title: string;
  rows: TipRow[];
  note?: string;
}

interface TipApi {
  show: (tip: Tip) => void;
  hide: () => void;
}

const TipContext = createContext<TipApi>({ show: () => {}, hide: () => {} });

export function TooltipProvider({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = useCallback((t: Tip) => setTip(t), []);
  const hide = useCallback(() => setTip(null), []);
  const api = useMemo(() => ({ show, hide }), [show, hide]);

  let style: React.CSSProperties | undefined;
  if (tip) {
    const flipX = tip.x > window.innerWidth - 260;
    const flipY = tip.y > window.innerHeight - 160;
    style = {
      left: flipX ? undefined : tip.x + 14,
      right: flipX ? window.innerWidth - tip.x + 14 : undefined,
      top: flipY ? undefined : tip.y + 14,
      bottom: flipY ? window.innerHeight - tip.y + 14 : undefined,
    };
  }

  return (
    <TipContext.Provider value={api}>
      {children}
      {tip && (
        <div className="tooltip" role="status" style={style}>
          <div className="tooltip-title">{tip.title}</div>
          {tip.rows.map((r, i) => (
            <div className="tooltip-row" key={i}>
              {r.color && (
                <span
                  className="tooltip-key"
                  style={r.hollow ? { borderColor: r.color } : { background: r.color, borderColor: r.color }}
                />
              )}
              <span className="tooltip-value">{r.value}</span>
              <span className="tooltip-label">{r.label}</span>
            </div>
          ))}
          {tip.note && <div className="tooltip-note">{tip.note}</div>}
        </div>
      )}
    </TipContext.Provider>
  );
}

export const useTip = () => useContext(TipContext);
