export interface AnswerPart {
  kind: "text" | "code";
  lang: string;
  body: string;
}

/** Split on ``` fences: even chunks are prose, odd chunks are code (first line may name the language). */
export function splitFences(text: string): AnswerPart[] {
  return text
    .split("```")
    .map((chunk, i): AnswerPart => {
      if (i % 2 === 0) return { kind: "text", lang: "", body: chunk.trim() };
      const nl = chunk.indexOf("\n");
      const first = nl >= 0 ? chunk.slice(0, nl).trim() : "";
      const hasLang = nl >= 0 && /^[\w+#.-]*$/.test(first);
      return { kind: "code", lang: hasLang ? first : "", body: (hasLang ? chunk.slice(nl + 1) : chunk).replace(/\n$/, "") };
    })
    .filter((p) => p.body !== "");
}

/** Plain preformatted text, with fenced code blocks kept in their own scrollable boxes. */
export function AnswerText({ text }: { text: string }) {
  return (
    <div>
      {splitFences(text).map((p, i) =>
        p.kind === "text" ? (
          <p key={i} className="answer-text">{p.body}</p>
        ) : (
          <div key={i} className="code-block">
            {p.lang && <span className="lang">{p.lang}</span>}
            <pre>
              <code>{p.body}</code>
            </pre>
          </div>
        ),
      )}
    </div>
  );
}
