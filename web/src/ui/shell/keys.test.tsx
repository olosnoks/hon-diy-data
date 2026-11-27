import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetSession, useSession } from '../../core/session';
import { handleKey, useGlobalKeys } from './keys';

beforeEach(() => {
  localStorage.clear();
  resetSession();
});
afterEach(cleanup);

describe('handleKey', () => {
  it('F1-F8 pick the screens in order', () => {
    const ids = ['live', 'drive', 'events', 'replay', 'power', 'gps', 'bytes', 'setup'];
    ids.forEach((id, i) => {
      expect(handleKey({ key: `F${i + 1}` })).toBe(true);
      expect(useSession.getState().screen).toBe(id);
    });
  });

  it('F9 toggles Dracula and Darker', () => {
    handleKey({ key: 'F9' });
    expect(useSession.getState().theme).toBe('darker');
    handleKey({ key: 'F9' });
    expect(useSession.getState().theme).toBe('dracula');
  });

  it('Space marks once per press (auto-repeat ignored)', () => {
    expect(handleKey({ key: ' ' })).toBe(true);
    expect(handleKey({ key: ' ', repeat: true })).toBe(true);
    expect(useSession.getState().marks.length).toBe(1);
  });

  it('Space in a text field types, F-keys and Esc still work', () => {
    const input = document.createElement('input');
    document.body.appendChild(input);
    expect(handleKey({ key: ' ', target: input })).toBe(false);
    expect(useSession.getState().marks.length).toBe(0);
    expect(handleKey({ key: 'F2', target: input })).toBe(true);
    expect(useSession.getState().screen).toBe('drive');
    useSession.getState().openPanel('raw');
    expect(handleKey({ key: 'Escape', target: input })).toBe(true);
    expect(useSession.getState().panel).toBeNull();
    input.remove();
  });

  it('a checkbox does not count as typing', () => {
    const box = document.createElement('input');
    box.type = 'checkbox';
    expect(handleKey({ key: ' ', target: box })).toBe(true);
    expect(useSession.getState().marks.length).toBe(1);
  });

  it('Esc closes the panel, then acknowledges alarms', () => {
    const s = useSession.getState();
    s.setAlarms([{ name: 'x', channel: 'rpm', level: 'alarm', message: '', acked: false }]);
    s.openPanel('raw');
    handleKey({ key: 'Escape' });
    expect(useSession.getState().panel).toBeNull();
    expect(useSession.getState().alarms[0]!.acked).toBe(false);
    handleKey({ key: 'Escape' });
    expect(useSession.getState().alarms[0]!.acked).toBe(true);
  });

  it('Ctrl+R starts and stops recording', () => {
    handleKey({ key: 'r', ctrlKey: true });
    expect(useSession.getState().recording.active).toBe(true);
    handleKey({ key: 'r', ctrlKey: true });
    expect(useSession.getState().recording.active).toBe(false);
    expect(handleKey({ key: 'r' })).toBe(false);
  });

  it('leaves other keys alone', () => {
    expect(handleKey({ key: 'a' })).toBe(false);
    expect(handleKey({ key: 'F10' })).toBe(false);
    expect(handleKey({ key: 'F5', altKey: true })).toBe(false);
  });
});

function Keys() {
  useGlobalKeys();
  return <div>keys</div>;
}

describe('useGlobalKeys', () => {
  it('handles window keydown and prevents the browser default (F5 must not reload)', () => {
    render(<Keys />);
    expect(screen.getByText('keys')).toBeTruthy();
    const ev = new KeyboardEvent('keydown', { key: 'F5', cancelable: true });
    act(() => { window.dispatchEvent(ev); });
    expect(ev.defaultPrevented).toBe(true);
    expect(useSession.getState().screen).toBe('power');
    const other = new KeyboardEvent('keydown', { key: 'x', cancelable: true });
    act(() => { window.dispatchEvent(other); });
    expect(other.defaultPrevented).toBe(false);
  });

  it('removes the listener on unmount', () => {
    const { unmount } = render(<Keys />);
    unmount();
    fireEvent.keyDown(window, { key: 'F2' });
    expect(useSession.getState().screen).toBe('live');
  });
});
