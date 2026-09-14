import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'fs';
import { join, resolve } from 'path';

/**
 * Drift guard: CSS custom properties used in app/ must be defined in app/globals.css.
 * Catches undefined variable references that degrade styling (like deepwiki vars after theme migration).
 */

// CSS variable names that are tailwind-internal or otherwise legitimately external
const ALLOWED_EXTERNAL_VARS = new Set([
  '--font-geist-sans',  // Legacy font variable from Mermaid.tsx vendored code
  '--font-serif-jp',    // Legacy font variable from Mermaid.tsx vendored code
  '--sidebar-width',       // shadcn Sidebar: set via inline style in SidebarProvider/Sidebar, not a globals.css token
  '--sidebar-width-icon',  // shadcn Sidebar: set via inline style in SidebarProvider, not a globals.css token
  '--skeleton-width',      // shadcn Sidebar (SidebarMenuSkeleton): set via inline style per-instance, not a globals.css token
  '--radix-dropdown-menu-content-available-height', // Radix popper: injected at runtime, not a theme token
  '--radix-dropdown-menu-trigger-width', // Radix popper: injected at runtime, not a theme token
  '--radix-dropdown-menu-content-transform-origin', // Radix popper: injected at runtime, not a theme token
  '--radix-tooltip-content-transform-origin', // Radix popper: injected at runtime, not a theme token
]);

function readGlobalStyles(): Set<string> {
  const globalsPath = resolve(__dirname, '../../globals.css');
  const content = readFileSync(globalsPath, 'utf-8');

  // Extract all custom property definitions: --name: value;
  const varPattern = /--[a-z0-9-]+(?=\s*:)/g;
  const matches = content.match(varPattern) || [];
  return new Set(matches);
}

function scanTsxFiles(dir: string, defined: Set<string>): Map<string, string[]> {
  const used: Map<string, string[]> = new Map();

  function walk(currentPath: string) {
    const entries = readdirSync(currentPath, { withFileTypes: true });

    for (const entry of entries) {
      const fullPath = join(currentPath, entry.name);

      // Skip __tests__ directories
      if (entry.isDirectory() && entry.name === '__tests__') {
        continue;
      }

      if (entry.isDirectory()) {
        walk(fullPath);
      } else if (entry.isFile() && (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts'))) {
        const content = readFileSync(fullPath, 'utf-8');

        // Find all var(--name) usages
        const varPattern = /var\((--[a-z0-9-]+)[),]/g;
        let match;

        while ((match = varPattern.exec(content)) !== null) {
          const varName = match[1];
          if (!used.has(varName)) {
            used.set(varName, []);
          }
          used.get(varName)!.push(fullPath);
        }
      }
    }
  }

  walk(dir);
  return used;
}

describe('CSS variable drift guard', () => {
  it('all var(--name) usages in app/ are defined in globals.css', () => {
    const defined = readGlobalStyles();
    const appPath = resolve(__dirname, '../..');
    const used = scanTsxFiles(appPath, defined);

    const undefined_vars: Array<[string, string[]]> = [];

    for (const [varName, locations] of used) {
      if (!defined.has(varName) && !ALLOWED_EXTERNAL_VARS.has(varName)) {
        undefined_vars.push([varName, locations]);
      }
    }

    if (undefined_vars.length > 0) {
      const report = undefined_vars
        .map(([varName, locs]) => {
          const uniqueLocs = [...new Set(locs)].slice(0, 3);
          const moreText = locs.length > 3 ? ` (+${locs.length - 3} more)` : '';
          return `  ${varName}: ${uniqueLocs.join(', ')}${moreText}`;
        })
        .join('\n');
      expect.fail(`Undefined CSS variables found:\n${report}`);
    }
  });
});
