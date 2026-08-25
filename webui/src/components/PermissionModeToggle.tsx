// Permission mode toggle — T1-2 深化
// Displays current permission mode (auto/ask/strict) and allows switching.

import { useState } from 'preact/hooks';
import { getPermissionMode, setPermissionMode, type PermissionMode } from '../api';
import { useT } from '../settings';

interface PermissionModeToggleProps {
  /** Called after successful mode switch */
  onModeChanged?: (mode: PermissionMode) => void;
}

export function PermissionModeToggle({ onModeChanged }: PermissionModeToggleProps) {
  const t = useT();
  const [mode, setModeState] = useState<PermissionMode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadMode() {
    try {
      const resp = await getPermissionMode();
      setModeState(resp.mode);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  // Load current mode on mount
  useState(() => { loadMode(); });

  async function switchMode(newMode: PermissionMode) {
    if (loading || newMode === mode) return;
    setLoading(true);
    setError(null);
    try {
      await setPermissionMode(newMode);
      setModeState(newMode);
      onModeChanged?.(newMode);
    } catch (e) {
      setError(t('perm.mode.switchFailed', { error: String(e) }));
    } finally {
      setLoading(false);
    }
  }

  if (error && !mode) {
    return (
      <div class="permission-mode-bar error">
        <span>{t('perm.mode.title')}: ⚠ {error}</span>
        <button class="btn btn-sm" onClick={loadMode}>重试</button>
      </div>
    );
  }

  const modes: PermissionMode[] = ['ask', 'auto', 'strict'];
  const descriptions = {
    ask: t('perm.mode.ask'),
    auto: t('perm.mode.auto'),
    strict: t('perm.mode.strict'),
  };

  return (
    <div class="permission-mode-bar">
      <span class="permission-mode-label">{t('perm.mode.title')}:</span>
      <div class="permission-mode-group">
        {modes.map((m) => (
          <button
            key={m}
            class={`permission-mode-btn ${m === mode ? 'active' : ''}`}
            disabled={loading || m === mode}
            onClick={() => switchMode(m)}
            title={descriptions[m]}
          >
            {m.toUpperCase()}
          </button>
        ))}
      </div>
      {mode && (
        <span class="permission-mode-hint">{descriptions[mode]}</span>
      )}
      {error && <span class="permission-mode-error">{error}</span>}
    </div>
  );
}
