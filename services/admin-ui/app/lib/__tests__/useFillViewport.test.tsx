import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useFillViewport } from '../useFillViewport';

let top = 100;
const observers: (() => void)[] = [];
const observed: Element[] = [];
beforeEach(() => {
  top = 100; observers.length = 0; observed.length = 0;
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ top } as DOMRect));
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { observers.push(callback); } observe(target: Element) { observed.push(target); } disconnect() {} });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function Filled() {
  const fill = useFillViewport<HTMLDivElement>();
  return <div data-testid="filled" ref={fill.ref} style={fill.style} />;
}

test('the height fills the screen from the container top, less the 1.5rem bottom gap', () => {
  render(<Filled />);
  expect(screen.getByTestId('filled').style.height).toBe('676px');  // 800 - 100 - 24
});

test('the height follows the window size and content above the container', () => {
  render(<Filled />);
  act(() => { Object.defineProperty(window, 'innerHeight', { configurable: true, value: 900 }); window.dispatchEvent(new Event('resize')); });
  expect(screen.getByTestId('filled').style.height).toBe('776px');
  act(() => { top = 200; observers.forEach(callback => callback()); });  // an alert appeared above it
  expect(screen.getByTestId('filled').style.height).toBe('676px');
});

test('the height is floored, not rounded, so fractional space never pushes the form past the screen', () => {
  top = 100.6;
  render(<Filled />);
  expect(screen.getByTestId('filled').style.height).toBe('675px');  // floor(800 - 100.6 - 24) = floor(675.4)
  top = 100.3; observers.forEach(callback => act(callback));
  expect(screen.getByTestId('filled').style.height).toBe('675px');  // 675.7 would round up to 676
});

test('it re-measures when the container parent resizes (an alert above it disappears)', () => {
  render(<div data-testid="parent"><Filled /></div>);
  expect(observed).toContain(screen.getByTestId('parent'));
});

test('on a tiny screen the height stops at 26rem, so the page scrolls instead of collapsing the panes', () => {
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 300 });
  render(<Filled />);
  expect(screen.getByTestId('filled').style.height).toBe('416px');
});
