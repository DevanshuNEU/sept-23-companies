import { openExternal, safeHref } from './safe-href';

describe('safeHref', () => {
  it.each(['https://boards.example/jobs/1', 'http://careers.example/x?y=1#z', 'HTTPS://Upper.example/'])('keeps %s', (url) => {
    expect(safeHref(url)).toBe(url);
  });

  it.each([
    'javascript:alert(1)',
    ' javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
    'mailto:ava@example.com',
    'ftp://files.example/a',
    '//evil.example/x',
    '/relative/path',
    'not a url',
    '',
  ])('refuses %j', (url) => {
    expect(safeHref(url)).toBeUndefined();
  });

  it('refuses null and undefined', () => {
    expect(safeHref(null)).toBeUndefined();
    expect(safeHref(undefined)).toBeUndefined();
  });
});

describe('openExternal', () => {
  afterEach(() => vi.restoreAllMocks());

  it('opens a web address in a new tab with noopener', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    expect(openExternal('https://boards.example/jobs/1')).toBe(true);
    expect(open).toHaveBeenCalledWith('https://boards.example/jobs/1', '_blank', 'noopener,noreferrer');
  });

  it('opens nothing for javascript:', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    expect(openExternal('javascript:alert(1)')).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });
});
