/*
 * INTENTIONALLY VULNERABLE demo component (cicd-ai-flow vuln-samples). Never ship this.
 *
 * SonarQube findings (measured on SonarQube 26.11, reported as Vulnerability issues):
 *  - S1523  eval of user input: detected, CRITICAL
 *  - S2245  Math.random() token: detected, MAJOR
 *  - S2068 / S6418  hard-coded password / API key: rules active but not raised on these literals
 *  - dangerouslySetInnerHTML: no SonarJS rule for the React form (S6299 is Vue-only), visual example only
 *           (DESIGN.md lists it as a hotspot example; keep for the AI code review demo)
 *
 * The file must still pass `npm run lint` and `tsc -b`, so the impure calls live in event handlers
 * (react-hooks/purity forbids them during render).
 */
import { useState } from 'react';

export const API_KEY = 'f3a9c1e7b2d84f6a9c0e1b2d3f4a5c6e7d8f9a0b'; // S6418: hard-coded secret
export const ADMIN_PASSWORD = 'admin123!'; // S2068: hard-coded password

export function UnsafeHtml() {
  const [markup, setMarkup] = useState('<b>hello</b>');
  const [expression, setExpression] = useState('1 + 1');
  const [result, setResult] = useState('');
  const [sessionToken, setSessionToken] = useState('');

  const evaluate = () => {
    setResult(String(eval(expression))); // S1523: eval of user input
  };

  const issueToken = () => {
    setSessionToken(Math.random().toString(36).slice(2)); // S2245: insecure token
  };

  return (
    <section>
      <h2>Unsafe HTML demo</h2>
      <input aria-label="markup" value={markup} onChange={(event) => setMarkup(event.target.value)} />
      <div dangerouslySetInnerHTML={{ __html: markup }} /> {/* S6299: raw user-controlled HTML */}
      <input aria-label="expression" value={expression} onChange={(event) => setExpression(event.target.value)} />
      <button type="button" onClick={evaluate}>
        eval
      </button>
      <output>{result}</output>
      <button type="button" onClick={issueToken}>
        issue token
      </button>
      <p>
        token: {sessionToken} (key: {API_KEY.slice(0, 4)}…, admin password length: {ADMIN_PASSWORD.length})
      </p>
    </section>
  );
}
