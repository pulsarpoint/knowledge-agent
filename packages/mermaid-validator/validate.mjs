import { JSDOM } from 'jsdom';

// The parser needs DOMPurify's DOM APIs. No scripts or remote resources run.
const dom = new JSDOM('');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const { default: mermaid } = await import('mermaid');
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true });

export async function validate(chart) {
  if (typeof chart !== 'string' || !chart.trim() || chart.length > 40000) {
    return { valid: false, error: 'Use a nonempty diagram of at most 40,000 characters.' };
  }
  try {
    await mermaid.parse(chart);
    return { valid: true };
  } catch (error) {
    return { valid: false, error: String(error?.message ?? error).slice(0, 1200) };
  }
}

if (process.argv[1] && import.meta.url === new URL(process.argv[1], 'file:').href) {
  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
    if (input.length > 240010) process.exit(2);
  }
  try {
    process.stdout.write(JSON.stringify(await validate(JSON.parse(input))));
  } catch {
    process.exitCode = 2;
  } finally {
    dom.window.close();
  }
}
