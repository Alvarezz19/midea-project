import '@testing-library/jest-dom/vitest';

const originalConsoleError = console.error;

// React 19 在 jsdom 中会把 Ant Design 动画、测量和 Portal 的异步刷新报告为 act 噪音。
console.error = (...args: unknown[]) => {
  const message = args.map(String).join(' ');
  if (message.includes('was not wrapped in act(...)')) {
    return;
  }
  originalConsoleError(...args);
};

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverMock);

vi.stubGlobal('matchMedia', (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn()
}));

const jsdomGetComputedStyle = window.getComputedStyle.bind(window);
vi.stubGlobal('getComputedStyle', (element: Element) => jsdomGetComputedStyle(element));
