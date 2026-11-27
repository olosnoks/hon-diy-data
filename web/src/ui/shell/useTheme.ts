import { useSession, type ThemeName } from '../../core/session';
import { DARKER, DRACULA, type Theme } from '../theme';

export function themeOf(name: ThemeName): Theme {
  return name === 'darker' ? DARKER : DRACULA;
}

/** The current theme tokens (Dracula or Darker, F9 toggles). Any screen may use it. */
export function useTheme(): Theme {
  return themeOf(useSession((s) => s.theme));
}
