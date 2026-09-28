/**
 * The part of highlight.js the panel uses, served from public/vendor/hljs
 * through the import map. Declared here because the vendored ES build ships
 * no types of its own.
 */
declare module 'hljs' {
  interface Result {
    /** HTML with the code escaped: only highlight.js's own spans in it. */
    value: string;
    language?: string;
  }
  interface HighlightJs {
    highlight(
      code: string,
      options: { language: string; ignoreIllegals?: boolean },
    ): Result;
    highlightAuto(code: string, languages?: string[]): Result;
    getLanguage(name: string): object | undefined;
  }
  const hljs: HighlightJs;
  export default hljs;
}
