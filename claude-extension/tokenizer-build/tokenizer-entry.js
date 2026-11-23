import { encode } from 'gpt-tokenizer/encoding/o200k_base';
globalThis.CUT_countTokens = (text) => {
  if (!text) return 0;
  try { return encode(text, { allowedSpecial: 'all' }).length; }
  catch { return Math.ceil(text.length / 4); }
};
