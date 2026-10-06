/* Token estimate shared by the content script and the popup: about 4 characters per token
   for English and code, about 1 per character for Chinese, Japanese and Korean. */
"use strict";
var AITT = globalThis.AITT || (globalThis.AITT = {});
AITT.estimateTokens = function (text) {
  if (!text) return 0;
  const s = String(text);
  let wide = 0;
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if ((c >= 0x3040 && c <= 0x30ff) || (c >= 0x4e00 && c <= 0x9fff) || (c >= 0xac00 && c <= 0xd7af)) wide++;
  }
  return wide + Math.ceil((s.length - wide) / 4);
};
