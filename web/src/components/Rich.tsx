import type { ReactNode } from 'react';

/** Minimal inline markup: **bold**, `code` and [text](url). Never uses innerHTML. */
export function Rich({ text }: { text: string }) {
  const out: ReactNode[] = [];
  const re = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<strong key={k++}>{m[1]}</strong>);
    else if (m[2]) out.push(<code key={k++}>{m[2]}</code>);
    else if (m[3]) {
      const href = m[4];
      const safe = /^(https?:|#|mailto:)/.test(href) ? href : '#';
      out.push(
        <a key={k++} href={safe} target={safe.startsWith('http') ? '_blank' : undefined} rel="noreferrer">
          {m[3]}
        </a>,
      );
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}
