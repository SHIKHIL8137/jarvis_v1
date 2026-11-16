import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Palette,
  Globe,
  Cpu,
  Database,
  Info,
  Check,
  Sun,
  Moon,
  Monitor,
  Download,
  Upload,
  Trash2,
  Mic,
  Key,
  Search,
  Brain,
  RefreshCw,
  Volume2,
  Play,
  Square as StopIcon,
} from 'lucide-react';
import { useAppStore, type ThemeMode } from '../lib/store';
import {
  checkHealth,
  fetchSpeechHealth,
  fetchTtsHealth,
  fetchVoiceProfiles,
  applyVoiceProfile,
  fetchAvailableVoices,
  updateSpeechSettings,
  synthesizeSpeech,
  type VoiceProfileInfo,
  type VoiceOption,
  getMemoryStats,
  getInferenceSource,
  setInferenceSource,
  getCloudKeyStatus,
  saveCloudKey,
  fetchToolCredentialStatus,
  saveToolCredentials,
  deleteToolCredential,
  getDesktopControl,
  setDesktopControl,
  fetchFolderGrants,
  addFolderGrant,
  revokeFolderGrant,
  isTauri,
  type InferenceSource,
  type MemoryStats,
  fetchBrowserStatus,
  fetchBrowserTabs,
  openOrReuseBrowserTab,
  activateBrowserTab,
  closeBrowserTab,
  refreshBrowserConnection,
  type BrowserStatus,
  type BrowserTab,
} from '../lib/api';
import { isAutoUpdateDisabled, setAutoUpdateDisabled } from '../components/Desktop/UpdateChecker';

const CLOUD_KEY_STATUS_CHANGED = 'openjarvis-cloud-key-status-changed';

function OllamaModelList() {
  const [models, setModels] = useState<Array<{ name: string; size: number }>>([]);
  useEffect(() => {
    fetch('http://localhost:11434/api/tags')
      .then(r => r.json())
      .then(data => setModels((data.models || []).map((m: any) => ({ name: m.name, size: m.size }))))
      .catch(() => setModels([]));
  }, []);
  if (models.length === 0) return <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>No models loaded</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {models.map(m => (
        <span key={m.name} className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px]"
          style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-success)', display: 'inline-block' }} />
          {m.name} ({(m.size / 1e9).toFixed(1)} GB)
        </span>
      ))}
    </div>
  );
}

function ApiKeyInput({
  keyName,
  placeholder,
  toolName,
}: {
  keyName: string;
  placeholder: string;
  toolName?: string;
}) {
  const [value, setValue] = useState('');
  const [saved, setSaved] = useState(false);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState('');
  const desktopKeyStorage = isTauri();
  const serverToolStorage = !desktopKeyStorage && !!toolName;
  const canManage = desktopKeyStorage || serverToolStorage;

  const refresh = useCallback(async () => {
    if (!canManage) {
      setHasKey(false);
      return;
    }
    try {
      const status = desktopKeyStorage
        ? await getCloudKeyStatus()
        : await fetchToolCredentialStatus(toolName!);
      setHasKey(!!status[keyName]);
    } catch {
      setHasKey(false);
    }
  }, [canManage, desktopKeyStorage, keyName, toolName]);

  useEffect(() => {
    void refresh();
    window.addEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
    return () => window.removeEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
  }, [refresh]);

  const save = async (v: string) => {
    const next = v.trim();
    if (!next) return;
    setError('');
    try {
      if (desktopKeyStorage) {
        await saveCloudKey(keyName, next);
      } else if (toolName) {
        await saveToolCredentials(toolName, { [keyName]: next });
      } else {
        return;
      }
      setValue('');
      setHasKey(true);
      setSaved(true);
      window.dispatchEvent(new Event(CLOUD_KEY_STATUS_CHANGED));
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e?.message || 'Failed to save API key');
    }
  };

  const remove = async () => {
    setError('');
    try {
      if (desktopKeyStorage) {
        await saveCloudKey(keyName, '');
      } else if (toolName) {
        await deleteToolCredential(toolName, keyName);
      } else {
        return;
      }
      setValue('');
      setHasKey(false);
      setSaved(true);
      window.dispatchEvent(new Event(CLOUD_KEY_STATUS_CHANGED));
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e?.message || 'Failed to remove API key');
    }
  };

  return (
    <div className="flex items-center gap-2">
      <input
        type="password"
        value={value}
        onChange={e => setValue(e.target.value)}
        onBlur={() => { if (value.trim()) void save(value); }}
        placeholder={hasKey ? (desktopKeyStorage ? 'Saved in secure storage' : 'Saved by local server') : placeholder}
        disabled={!canManage}
        className="w-48 px-2 py-1 rounded text-xs"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
      {hasKey && (
        <button
          onClick={() => void remove()}
          className="px-2 py-1 rounded text-[10px] cursor-pointer"
          style={{ color: 'var(--color-error)', border: '1px solid var(--color-error)' }}
        >
          Remove
        </button>
      )}
      {saved && <span className="text-[10px]" style={{ color: 'var(--color-success)' }}>Saved</span>}
      {error && <span className="text-[10px]" style={{ color: 'var(--color-error)' }}>{error}</span>}
    </div>
  );
}

function CloudProviderStatus({ label, keyName }: { label: string; keyName: string }) {
  const [hasKey, setHasKey] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const status = await getCloudKeyStatus();
      setHasKey(!!status[keyName]);
    } catch {
      setHasKey(false);
    }
  }, [keyName]);

  useEffect(() => {
    void refresh();
    window.addEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
    return () => window.removeEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
  }, [refresh]);

  return (
    <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', display: 'inline-block',
        background: hasKey ? 'var(--color-success)' : 'var(--color-text-tertiary)',
      }} />
      {label}
    </span>
  );
}

/** Master switch: may Jarvis click, type and press keys on this PC without asking each time? */
function DesktopControlToggle() {
  const [on, setOn] = useState(false);
  useEffect(() => { getDesktopControl().then(setOn).catch(() => {}); }, []);
  const flip = async () => {
    await setDesktopControl(!on);
    setOn(!on);
  };
  return (
    <div className="flex items-center justify-between py-2 gap-4">
      <div>
        <div className="text-sm" style={{ color: 'var(--color-text)' }}>Desktop control</div>
        <div className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
          Let Jarvis operate apps by clicking, typing and pressing keys without asking each time.
          Opening apps and reading the screen never need this. It never types into login, password or payment windows.
        </div>
      </div>
      <button
        onClick={flip}
        role="switch"
        aria-checked={on}
        className="relative w-11 h-6 rounded-full cursor-pointer shrink-0"
        style={{ background: on ? 'var(--color-accent)' : 'var(--color-bg-tertiary)' }}
      >
        <span
          className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform"
          style={{ transform: on ? 'translateX(20px)' : 'translateX(0)' }}
        />
      </button>
    </div>
  );
}

function DesktopBrowserIntegration() {
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [loading, setLoading] = useState(false);
  const [testUrl, setTestUrl] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [copiedToken, setCopiedToken] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([fetchBrowserStatus(), fetchBrowserTabs()]);
      setStatus(s);
      setTabs(t);
    } catch {
      // offline or not ready
    }
  }, []);

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 5000);
    return () => clearInterval(timer);
  }, [loadData]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      const s = await refreshBrowserConnection();
      setStatus(s);
      const t = await fetchBrowserTabs();
      setTabs(t);
    } catch (e: any) {
      setActionMessage(e?.message || 'Refresh failed');
    } finally {
      setLoading(false);
    }
  };

  const handleOpenOrReuse = async (explicitNewTab = false) => {
    if (!testUrl.trim()) return;
    setLoading(true);
    setActionMessage('');
    try {
      const res = await openOrReuseBrowserTab(testUrl.trim(), explicitNewTab);
      setActionMessage(res.message);
      await loadData();
    } catch (e: any) {
      setActionMessage(e?.message || 'Action failed');
    } finally {
      setLoading(false);
    }
  };

  const handleActivate = async (tabId: string | number, windowId = 0, title = '') => {
    try {
      await activateBrowserTab(tabId, windowId, title);
      setActionMessage(`Focused tab ${tabId}`);
      await loadData();
    } catch (e: any) {
      setActionMessage(e?.message || 'Activation failed');
    }
  };

  const handleClose = async (tabId: string | number) => {
    try {
      await closeBrowserTab(tabId);
      setActionMessage(`Closed tab ${tabId}`);
      await loadData();
    } catch (e: any) {
      setActionMessage(e?.message || 'Close failed');
    }
  };

  const copyToken = () => {
    if (status?.pairing_token) {
      navigator.clipboard.writeText(status.pairing_token);
      setCopiedToken(true);
      setTimeout(() => setCopiedToken(false), 2000);
    }
  };

  const isExtensionConnected = status?.extension_connected;
  const isRunning = status?.browser_running;

  return (
    <div className="space-y-4 py-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>
            Desktop Browser Control & Tab Reuse
          </div>
          <div className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
            Reuses existing desktop tabs (e.g. LinkedIn, GitHub) instead of opening duplicates.
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isExtensionConnected ? (
            <span className="px-2 py-0.5 rounded text-[11px] font-medium" style={{ background: '#064e3b', color: '#34d399', border: '1px solid #059669' }}>
              Companion Active
            </span>
          ) : isRunning ? (
            <span className="px-2 py-0.5 rounded text-[11px] font-medium" style={{ background: '#1e3a5f', color: '#60a5fa', border: '1px solid #2563eb' }}>
              Desktop Detected
            </span>
          ) : (
            <span className="px-2 py-0.5 rounded text-[11px] font-medium" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-tertiary)' }}>
              Disconnected
            </span>
          )}
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="p-1 rounded cursor-pointer"
            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}
            title="Refresh status and open tabs"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Diagnostics info */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        <div className="p-2 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>
          <div style={{ color: 'var(--color-text-tertiary)' }}>Connection Mode</div>
          <div className="font-semibold capitalize" style={{ color: 'var(--color-text)' }}>
            {status?.mode === 'extension' ? 'Companion Bridge' : status?.mode === 'cdp' ? 'CDP DevTools' : status?.mode === 'windows_gui' ? 'Windows GUI' : 'Unconnected'}
          </div>
        </div>
        <div className="p-2 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>
          <div style={{ color: 'var(--color-text-tertiary)' }}>Running Process</div>
          <div className="font-semibold" style={{ color: 'var(--color-text)' }}>
            {status?.running_browsers?.join(', ') || 'None detected'}
          </div>
        </div>
        <div className="p-2 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>
          <div style={{ color: 'var(--color-text-tertiary)' }}>Open Tabs</div>
          <div className="font-semibold" style={{ color: 'var(--color-text)' }}>
            {status?.tab_count ?? tabs.length}
          </div>
        </div>
        <div className="p-2 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>
          <div style={{ color: 'var(--color-text-tertiary)' }}>Focused Tab</div>
          <div className="font-semibold truncate" style={{ color: 'var(--color-text)' }} title={status?.active_tab?.title || ''}>
            {status?.active_tab?.title || 'None'}
          </div>
        </div>
      </div>

      {/* URL test box */}
      <div className="p-3 rounded space-y-2" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <div className="text-xs font-medium" style={{ color: 'var(--color-text)' }}>
          Test Tab Reuse or Launch
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={testUrl}
            onChange={(e) => setTestUrl(e.target.value)}
            placeholder="e.g. https://www.linkedin.com or linkedin"
            className="flex-1 px-2.5 py-1.5 text-xs rounded outline-none"
            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
          />
          <button
            onClick={() => handleOpenOrReuse(false)}
            disabled={loading || !testUrl.trim()}
            className="px-3 py-1.5 text-xs rounded font-medium cursor-pointer"
            style={{ background: 'var(--color-accent)', color: '#fff' }}
          >
            Open / Reuse
          </button>
          <button
            onClick={() => handleOpenOrReuse(true)}
            disabled={loading || !testUrl.trim()}
            className="px-3 py-1.5 text-xs rounded font-medium cursor-pointer"
            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
          >
            Force New Tab
          </button>
        </div>
        {actionMessage && (
          <div className="text-xs p-2 rounded" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}>
            {actionMessage}
          </div>
        )}
      </div>

      {/* Tabs list */}
      {tabs.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium px-1" style={{ color: 'var(--color-text-tertiary)' }}>
            Detected Browser Tabs ({tabs.length})
          </div>
          <div className="max-h-48 overflow-y-auto space-y-1 rounded p-1" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
            {tabs.map((tab) => (
              <div
                key={String(tab.tab_id)}
                className="flex items-center justify-between p-1.5 rounded text-xs gap-2"
                style={{ background: tab.is_active ? 'var(--color-bg-tertiary)' : 'transparent' }}
              >
                <div className="truncate flex-1">
                  <span className="font-medium" style={{ color: 'var(--color-text)' }}>
                    {tab.title || 'Untitled'}
                  </span>
                  <span className="ml-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
                    ({tab.domain || tab.url || 'window'})
                  </span>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => handleActivate(tab.tab_id, Number(tab.window_id) || 0, tab.title)}
                    className="px-2 py-0.5 rounded text-[11px] font-medium cursor-pointer"
                    style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}
                  >
                    Focus
                  </button>
                  <button
                    onClick={() => handleClose(tab.tab_id)}
                    className="px-2 py-0.5 rounded text-[11px] cursor-pointer hover:text-red-400"
                    style={{ color: 'var(--color-text-tertiary)' }}
                  >
                    Close
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Extension setup guide card */}
      <div className="p-3 rounded text-xs space-y-2" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <div className="font-medium" style={{ color: 'var(--color-text)' }}>
          Chrome / Edge Companion Extension Setup
        </div>
        <div style={{ color: 'var(--color-text-tertiary)', lineHeight: 1.5 }}>
          To enable instant tab reuse across your personal Chrome or Edge windows:
          <ol className="list-decimal ml-4 mt-1 space-y-0.5">
            <li>Open <code className="px-1 py-0.2 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>chrome://extensions</code> or <code className="px-1 py-0.2 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>edge://extensions</code> and enable <b>Developer mode</b>.</li>
            <li>Click <b>Load unpacked</b> and select the folder: <code className="px-1 py-0.2 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>extensions/jarvis-browser-extension</code>.</li>
            <li>The companion connects automatically using your local pairing secret.</li>
          </ol>
        </div>
        <div className="flex items-center gap-2 pt-1">
          <span style={{ color: 'var(--color-text-tertiary)' }}>Local Pairing Token:</span>
          <code className="px-1.5 py-0.5 rounded font-mono text-[11px]" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}>
            {status?.pairing_token ? `${status.pairing_token.slice(0, 12)}...` : 'Loading...'}
          </code>
          <button
            onClick={copyToken}
            className="px-2 py-0.5 rounded text-[11px] cursor-pointer"
            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}
          >
            {copiedToken ? 'Copied!' : 'Copy Token'}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Folders Jarvis may work in without asking again (risky commands still ask). */
function FolderPermissions() {
  const [folders, setFolders] = useState<string[]>([]);
  const [path, setPath] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(() => {
    fetchFolderGrants().then(setFolders).catch(() => setFolders([]));
  }, []);
  useEffect(refresh, [refresh]);

  const add = async () => {
    setError('');
    try {
      await addFolderGrant(path.trim());
      setPath('');
      refresh();
    } catch (e: any) {
      setError(e?.message || 'Could not grant folder');
    }
  };

  const box = { background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' };
  return (
    <div className="flex flex-col gap-2 py-2">
      {folders.length === 0 && (
        <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
          No folders granted yet. Use &quot;Approve &amp; always allow&quot; in the approvals bell, or add one below.
        </span>
      )}
      {folders.map((f) => (
        <div key={f} className="flex items-center justify-between gap-3 text-sm">
          <span className="font-mono text-xs truncate" style={{ color: 'var(--color-text)' }}>{f}</span>
          <button
            onClick={() => revokeFolderGrant(f).then(refresh)}
            className="text-xs px-2 py-1 rounded-lg cursor-pointer"
            style={{ color: 'var(--color-error)', border: '1px solid var(--color-error)' }}
          >
            Revoke
          </button>
        </div>
      ))}
      <div className="flex gap-2 mt-1">
        <input
          type="text"
          value={path}
          onChange={(e) => { setPath(e.target.value); setError(''); }}
          onKeyDown={(e) => { if (e.key === 'Enter' && path.trim()) void add(); }}
          placeholder="D:\projects\my-app"
          className="flex-1 text-sm px-3 py-1.5 rounded-lg outline-none font-mono"
          style={box}
        />
        <button
          onClick={add}
          disabled={!path.trim()}
          className="text-sm px-3 py-1.5 rounded-lg cursor-pointer disabled:opacity-40"
          style={{ ...box, background: 'var(--color-accent, var(--color-bg-tertiary))' }}
        >
          Grant
        </button>
      </div>
      {error && <span className="text-xs" style={{ color: 'var(--color-error)' }}>{error}</span>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-xl p-5"
      style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
    >
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--color-text)' }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function SettingRow({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-3" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
      <div className="min-w-0">
        <div className="text-sm" style={{ color: 'var(--color-text)' }}>{label}</div>
        {description && (
          <div className="text-xs mt-0.5 break-words" style={{ color: 'var(--color-text-tertiary)' }}>{description}</div>
        )}
      </div>
      <div>{children}</div>
    </div>
  );
}

type MemoryStatus =
  | { kind: 'loading' }
  | { kind: 'ready'; stats: MemoryStats }
  | { kind: 'error'; message: string };

export function MemoryStatusRow({
  status,
  onRetry,
}: {
  status: MemoryStatus;
  onRetry: () => void;
}) {
  const stats = status.kind === 'ready' ? status.stats : null;
  const description = status.kind === 'error'
    ? status.message
    : status.kind === 'loading'
      ? 'Checking memory backend...'
      : stats?.backend === 'none'
        ? 'Memory is not configured'
        : `${stats?.backend} backend — ${stats?.entries} entries`;
  const label = status.kind === 'loading'
    ? 'Checking...'
    : stats?.backend === 'none'
      ? 'Not configured'
      : stats
        ? `${stats.entries} entries`
        : 'Unavailable';

  return (
    <SettingRow label="Memory status" description={description}>
      <div className="flex items-center gap-2">
        <Brain size={14} style={{ color: stats && stats.backend !== 'none' ? 'var(--color-accent)' : 'var(--color-text-tertiary)' }} />
        <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-text-secondary)' }}>
          {label}
        </span>
        {status.kind === 'error' && (
          <button
            type="button"
            onClick={onRetry}
            className="text-xs underline cursor-pointer whitespace-nowrap"
            style={{ color: 'var(--color-accent)' }}
          >
            Retry
          </button>
        )}
      </div>
    </SettingRow>
  );
}

const themeOptions: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
];

export function SettingsPage() {
  const settings = useAppStore((s) => s.settings);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const conversations = useAppStore((s) => s.conversations);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [speechBackendAvailable, setSpeechBackendAvailable] = useState<boolean | null>(null);
  const [ttsBackend, setTtsBackend] = useState<{ available: boolean; backend?: string; voice_id?: string; speed?: number } | null>(null);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfileInfo[]>([]);
  const [activeProfile, setActiveProfile] = useState<string>('classic_jarvis');
  const [availableVoices, setAvailableVoices] = useState<VoiceOption[]>([]);
  const [selectedVoice, setSelectedVoice] = useState<string>('en-GB-RyanNeural');
  const [voiceSpeed, setVoiceSpeed] = useState<number>(0.96);
  const [isTestingVoice, setIsTestingVoice] = useState<boolean>(false);
  const testAudioRef = useRef<HTMLAudioElement | null>(null);
  const [saved, setSaved] = useState(false);

  const [autoUpdateEnabled, setAutoUpdateEnabled] = useState(() => !isAutoUpdateDisabled());
  const [updateCheckState, setUpdateCheckState] = useState<'idle' | 'checking' | 'available' | 'latest'>('idle');

  const handleAutoUpdateToggle = useCallback((enabled: boolean) => {
    setAutoUpdateEnabled(enabled);
    setAutoUpdateDisabled(!enabled);
  }, []);

  const handleCheckNow = useCallback(async () => {
    if (!(window as any).__TAURI_INTERNALS__) return;
    setUpdateCheckState('checking');
    try {
      const { check } = await import('@tauri-apps/plugin-updater');
      const update = await check();
      setUpdateCheckState(update ? 'available' : 'latest');
      setTimeout(() => setUpdateCheckState('idle'), 4000);
    } catch {
      setUpdateCheckState('idle');
    }
  }, []);

  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus>({ kind: 'loading' });
  const memoryRequestId = useRef(0);
  const [memoryEnabled, setMemoryEnabled] = useState(() => {
    try { return localStorage.getItem('openjarvis-memory-enabled') !== 'false'; } catch { return true; }
  });
  const [memoryBackend, setMemoryBackend] = useState(() => {
    try { return localStorage.getItem('openjarvis-memory-backend') || 'sqlite'; } catch { return 'sqlite'; }
  });
  const [memoryTopK, setMemoryTopK] = useState(() => {
    try { return parseInt(localStorage.getItem('openjarvis-memory-top-k') || '5'); } catch { return 5; }
  });
  const [memoryMinScore, setMemoryMinScore] = useState(() => {
    try { return parseFloat(localStorage.getItem('openjarvis-memory-min-score') || '0.1'); } catch { return 0.1; }
  });
  const [memoryMaxTokens, setMemoryMaxTokens] = useState(() => {
    try { return parseInt(localStorage.getItem('openjarvis-memory-max-tokens') || '2048'); } catch { return 2048; }
  });

  const [srcKind, setSrcKind] = useState<InferenceSource['kind']>('ollama');
  const [customHost, setCustomHost] = useState('http://localhost:1234/v1');
  const [customModel, setCustomModel] = useState('');
  const [customEngine, setCustomEngine] = useState('lmstudio');
  const [customKey, setCustomKey] = useState('');
  const [srcMsg, setSrcMsg] = useState('');

  useEffect(() => {
    getInferenceSource().then((s) => {
      setSrcKind(s.kind);
      if (s.host) setCustomHost(s.host);
      if (s.model) setCustomModel(s.model);
      if (s.engine) setCustomEngine(s.engine);
    }).catch(() => {});
  }, []);

  const saveSource = useCallback(async () => {
    try {
      if (srcKind === 'custom') {
        await setInferenceSource({ kind: 'custom', host: customHost, model: customModel, engine: customEngine, apiKey: customKey || undefined });
      } else {
        await setInferenceSource({ kind: 'ollama' });
      }
      setSrcMsg('Saved — restart the app to apply.');
    } catch (e: any) {
      setSrcMsg(e?.message ?? 'Failed to save.');
    }
  }, [srcKind, customHost, customModel, customEngine, customKey]);

  const refreshMemoryStatus = useCallback(async () => {
    const requestId = ++memoryRequestId.current;
    setMemoryStatus({ kind: 'loading' });
    try {
      const stats = await getMemoryStats();
      if (requestId === memoryRequestId.current) {
        setMemoryStatus({ kind: 'ready', stats });
      }
    } catch (error) {
      if (requestId === memoryRequestId.current) {
        setMemoryStatus({
          kind: 'error',
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }, []);

  useEffect(() => {
    void refreshMemoryStatus();
    const onFocus = () => { void refreshMemoryStatus(); };
    window.addEventListener('focus', onFocus);
    return () => {
      window.removeEventListener('focus', onFocus);
      ++memoryRequestId.current;
    };
  }, [refreshMemoryStatus, settings.apiUrl]);

  useEffect(() => {
    checkHealth().then(setHealthy);
    fetchSpeechHealth()
      .then((h) => setSpeechBackendAvailable(h.available))
      .catch(() => setSpeechBackendAvailable(false));
    fetchTtsHealth()
      .then((h) => {
        setTtsBackend(h);
        if (h.voice_id) setSelectedVoice(h.voice_id);
        if (typeof h.speed === 'number') setVoiceSpeed(h.speed);
      })
      .catch(() => setTtsBackend({ available: false }));
    fetchVoiceProfiles()
      .then((res) => {
        setVoiceProfiles(res.profiles || []);
        if (res.active_profile) setActiveProfile(res.active_profile);
        const curr = res.profiles?.find((p) => p.id === res.active_profile);
        if (curr) {
          setSelectedVoice(curr.voice_id);
          setVoiceSpeed(curr.speed);
        }
      })
      .catch(() => {});
    fetchAvailableVoices()
      .then((voices) => setAvailableVoices(voices || []))
      .catch(() => {});
  }, []);

  const handleProfileSelect = async (profileId: string) => {
    setActiveProfile(profileId);
    try {
      await applyVoiceProfile(profileId);
      const p = voiceProfiles.find((v) => v.id === profileId);
      if (p) {
        setSelectedVoice(p.voice_id);
        setVoiceSpeed(p.speed);
      }
      fetchTtsHealth().then(setTtsBackend).catch(() => {});
      showSaved();
    } catch {
      // ignore
    }
  };

  const handleVoiceChange = async (voiceId: string) => {
    setSelectedVoice(voiceId);
    try {
      await updateSpeechSettings({ voice_id: voiceId });
      fetchTtsHealth().then(setTtsBackend).catch(() => {});
      showSaved();
    } catch {
      // ignore
    }
  };

  const handleSpeedChange = async (speed: number) => {
    setVoiceSpeed(speed);
    try {
      await updateSpeechSettings({ voice_speed: speed });
      showSaved();
    } catch {
      // ignore
    }
  };

  const handlePreviewVoice = async () => {
    if (isTestingVoice) {
      if (testAudioRef.current) {
        testAudioRef.current.pause();
        testAudioRef.current = null;
      }
      setIsTestingVoice(false);
      return;
    }
    setIsTestingVoice(true);
    try {
      const phrase = "Good day, sir. Systems are online and fully operational. How may I assist you today?";
      const blob = await synthesizeSpeech(phrase, {
        voiceId: selectedVoice || undefined,
        speed: voiceSpeed,
      });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      testAudioRef.current = audio;
      audio.onended = () => {
        URL.revokeObjectURL(url);
        testAudioRef.current = null;
        setIsTestingVoice(false);
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        testAudioRef.current = null;
        setIsTestingVoice(false);
      };
      await audio.play();
    } catch {
      setIsTestingVoice(false);
    }
  };

  const showSaved = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const handleExport = () => {
    const data = localStorage.getItem('openjarvis-conversations') || '{}';
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `openjarvis-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        try {
          const data = JSON.parse(ev.target?.result as string);
          if (data.version === 1) {
            localStorage.setItem('openjarvis-conversations', JSON.stringify(data));
            useAppStore.getState().loadConversations();
            showSaved();
          }
        } catch {}
      };
      reader.readAsText(file);
    };
    input.click();
  };

  const [confirmClear, setConfirmClear] = useState(false);
  const handleClear = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 3000);
      return;
    }
    localStorage.removeItem('openjarvis-conversations');
    useAppStore.getState().loadConversations();
    setConfirmClear(false);
    showSaved();
  };

  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-2xl mx-auto">
        <header className="mb-6">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>
              Settings
            </h1>
            {saved && (
              <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full" style={{
                background: 'var(--color-accent-subtle)',
                color: 'var(--color-success)',
              }}>
                <Check size={12} /> Saved
              </span>
            )}
          </div>
          <p className="text-sm mt-2 max-w-2xl" style={{ color: 'var(--color-text-secondary)' }}>
            App preferences — appearance, model defaults, keyboard shortcuts, and data management.
          </p>
        </header>

        <div className="flex flex-col gap-4">
          {/* Appearance */}
          <Section title="Appearance">
            <SettingRow label="Theme" description="Choose how OpenJarvis looks">
              <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--color-bg-secondary)' }}>
                {themeOptions.map((opt) => {
                  const isActive = settings.theme === opt.value;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => { updateSettings({ theme: opt.value }); showSaved(); }}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer"
                      style={{
                        background: isActive ? 'var(--color-surface)' : 'transparent',
                        color: isActive ? 'var(--color-text)' : 'var(--color-text-tertiary)',
                        boxShadow: isActive ? 'var(--shadow-sm)' : 'none',
                      }}
                    >
                      <opt.icon size={14} />
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </SettingRow>
            <SettingRow label="Font size">
              <select
                value={settings.fontSize}
                onChange={(e) => { updateSettings({ fontSize: e.target.value as any }); showSaved(); }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <option value="small">Small</option>
                <option value="default">Default</option>
                <option value="large">Large</option>
              </select>
            </SettingRow>
          </Section>

          {/* Connection */}
          <Section title="Connection">
            <SettingRow label="Server status" description={serverInfo ? `${serverInfo.engine} / ${serverInfo.model}` : 'Not connected'}>
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ background: healthy === true ? 'var(--color-success)' : healthy === false ? 'var(--color-error)' : 'var(--color-text-tertiary)' }}
                />
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {healthy === true ? 'Connected' : healthy === false ? 'Disconnected' : 'Checking...'}
                </span>
              </div>
            </SettingRow>
            <SettingRow label="API URL" description="Set if backend runs on a different port or host">
              <input
                type="text"
                value={settings.apiUrl}
                onChange={(e) => { updateSettings({ apiUrl: e.target.value }); showSaved(); }}
                placeholder="http://localhost:8000"
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              />
            </SettingRow>
            <SettingRow label="API key" description="Required only if the server was started with an API key">
              <input
                type="password"
                value={settings.apiKey}
                onChange={(e) => { updateSettings({ apiKey: e.target.value }); showSaved(); }}
                placeholder="OPENJARVIS_API_KEY"
                autoComplete="off"
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              />
            </SettingRow>
          </Section>

          {/* Inference source */}
          <Section title="Inference source">
            <SettingRow label="Source" description="Where the app runs models. Applies after restart.">
              <select
                value={srcKind}
                onChange={(e) => { setSrcKind(e.target.value as InferenceSource['kind']); setSrcMsg(''); }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              >
                <option value="ollama">Bundled Ollama (default)</option>
                <option value="custom">Custom OpenAI-compatible server</option>
              </select>
            </SettingRow>
            {srcKind === 'custom' && (
              <>
                <SettingRow label="Server URL" description="e.g. LM Studio: http://localhost:1234/v1">
                  <input type="text" value={customHost} onChange={(e) => { setCustomHost(e.target.value); setSrcMsg(''); }} placeholder="http://localhost:1234/v1"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
                <SettingRow label="Model" description="Model id served by your endpoint">
                  <input type="text" value={customModel} onChange={(e) => { setCustomModel(e.target.value); setSrcMsg(''); }} placeholder="qwen2.5-7b-instruct"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
                <SettingRow label="Server type" description="OpenAI-compatible engine">
                  <select value={customEngine} onChange={(e) => { setCustomEngine(e.target.value); setSrcMsg(''); }}
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
                    <option value="lmstudio">LM Studio</option>
                    <option value="vllm">vLLM</option>
                    <option value="sglang">SGLang</option>
                    <option value="llamacpp">llama.cpp</option>
                    <option value="mlx">MLX</option>
                  </select>
                </SettingRow>
                <SettingRow label="API key (optional)" description="Only if your server requires one">
                  <input type="password" value={customKey} onChange={(e) => { setCustomKey(e.target.value); setSrcMsg(''); }} placeholder="leave blank if none"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
              </>
            )}
            <SettingRow label="" description={srcMsg}>
              <button onClick={saveSource}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{ background: 'var(--color-accent, var(--color-bg-tertiary))', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
                Save inference source
              </button>
            </SettingRow>
          </Section>

          {/* Folder permissions */}
          <Section title="Folder permissions">
            <FolderPermissions />
          </Section>

          <Section title="Desktop control">
            <DesktopControlToggle />
          </Section>

          <Section title="Desktop Browser Integration">
            <DesktopBrowserIntegration />
          </Section>

          {/* Models */}
          <Section title="Models">
            <SettingRow label="Local models (Ollama)" description="Models available for local inference">
              <OllamaModelList />
            </SettingRow>
            <div className="text-xs mt-2 px-1" style={{ color: 'var(--color-text-tertiary)' }}>
              Run <code className="px-1 py-0.5 rounded text-[11px]" style={{ background: 'var(--color-bg-tertiary)' }}>ollama pull &lt;model-name&gt;</code> in your terminal to add more models
            </div>
            <SettingRow label="Cloud providers" description="Green dot means API key is configured">
              <div className="flex flex-wrap gap-3">
                <CloudProviderStatus label="OpenAI" keyName="OPENAI_API_KEY" />
                <CloudProviderStatus label="Anthropic" keyName="ANTHROPIC_API_KEY" />
                <CloudProviderStatus label="Google" keyName="GEMINI_API_KEY" />
                <CloudProviderStatus label="OpenRouter" keyName="OPENROUTER_API_KEY" />
                <CloudProviderStatus label="Atlas Cloud" keyName="ATLASCLOUD_API_KEY" />
              </div>
            </SettingRow>
          </Section>

          {/* API Keys */}
          <Section title="API Keys">
            <SettingRow label="OpenAI" description="GPT-4, GPT-3.5, etc.">
              <ApiKeyInput keyName="OPENAI_API_KEY" placeholder="sk-..." />
            </SettingRow>
            <SettingRow label="Anthropic" description="Claude models">
              <ApiKeyInput keyName="ANTHROPIC_API_KEY" placeholder="sk-ant-..." />
            </SettingRow>
            <SettingRow label="Google" description="Gemini models">
              <ApiKeyInput keyName="GEMINI_API_KEY" placeholder="AI..." />
            </SettingRow>
            <SettingRow label="OpenRouter" description="Multi-provider routing">
              <ApiKeyInput keyName="OPENROUTER_API_KEY" placeholder="sk-or-..." />
            </SettingRow>
            <SettingRow label="Atlas Cloud" description="Models routed through Atlas Cloud">
              <ApiKeyInput keyName="ATLASCLOUD_API_KEY" placeholder="Atlas Cloud API key" />
            </SettingRow>
          </Section>

          {/* Tools */}
          <Section title="Tools">
            <SettingRow label="Web Search" description="Tavily key for web search tool">
              <ApiKeyInput keyName="TAVILY_API_KEY" placeholder="tvly-..." toolName="web_search" />
            </SettingRow>
          </Section>

          {/* Memory */}
          <Section title="Memory">
            <MemoryStatusRow status={memoryStatus} onRetry={() => { void refreshMemoryStatus(); }} />
            <SettingRow label="Use memory context" description="Automatically inject relevant memories into conversations">
              <button
                onClick={() => {
                  const next = !memoryEnabled;
                  setMemoryEnabled(next);
                  try { localStorage.setItem('openjarvis-memory-enabled', String(next)); } catch {}
                  showSaved();
                }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: memoryEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: memoryEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Memory backend" description="Which retrieval engine to use">
              <select
                value={memoryBackend}
                onChange={(e) => {
                  setMemoryBackend(e.target.value);
                  try { localStorage.setItem('openjarvis-memory-backend', e.target.value); } catch {}
                  showSaved();
                }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <option value="sqlite">sqlite</option>
                <option value="faiss">faiss</option>
                <option value="bm25">bm25</option>
                <option value="colbert">colbert</option>
                <option value="hybrid">hybrid</option>
              </select>
            </SettingRow>
            <SettingRow label="Results to inject" description={`${memoryTopK}`}>
              <input
                type="range"
                min="1"
                max="20"
                step="1"
                value={memoryTopK}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  setMemoryTopK(v);
                  try { localStorage.setItem('openjarvis-memory-top-k', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Min relevance score" description={`${memoryMinScore}`}>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={memoryMinScore}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  setMemoryMinScore(v);
                  try { localStorage.setItem('openjarvis-memory-min-score', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Max context tokens" description={`${memoryMaxTokens}`}>
              <input
                type="range"
                min="256"
                max="8192"
                step="256"
                value={memoryMaxTokens}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  setMemoryMaxTokens(v);
                  try { localStorage.setItem('openjarvis-memory-max-tokens', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
          </Section>

          {/* Model defaults */}
          <Section title="Model Defaults">
            <SettingRow label="Temperature" description={`${settings.temperature}`}>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={settings.temperature}
                onChange={(e) => { updateSettings({ temperature: parseFloat(e.target.value) }); showSaved(); }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Max tokens" description={`${settings.maxTokens}`}>
              <input
                type="range"
                min="256"
                max="32768"
                step="256"
                value={settings.maxTokens}
                onChange={(e) => { updateSettings({ maxTokens: parseInt(e.target.value) }); showSaved(); }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
          </Section>

          {/* Speech */}
          <Section title="Speech & Jarvis Voice">
            <SettingRow label="Speech-to-Text" description="Enable microphone input for voice dictation">
              <button
                onClick={() => { updateSettings({ speechEnabled: !settings.speechEnabled }); showSaved(); }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.speechEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.speechEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Text-to-Speech" description="Enable neural voice output on assistant replies">
              <button
                onClick={() => { updateSettings({ voiceOutputEnabled: !settings.voiceOutputEnabled }); showSaved(); }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.voiceOutputEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.voiceOutputEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Speak replies automatically" description="Read every assistant reply aloud as soon as it finishes">
              <button
                onClick={() => { updateSettings({ voiceAutoplay: !settings.voiceAutoplay }); showSaved(); }}
                disabled={!settings.voiceOutputEnabled}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.voiceAutoplay && settings.voiceOutputEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                  opacity: settings.voiceOutputEnabled ? 1 : 0.4,
                  cursor: settings.voiceOutputEnabled ? 'pointer' : 'default',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.voiceAutoplay && settings.voiceOutputEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>

            {/* Voice Profile Selector */}
            <SettingRow label="Voice Persona" description="Select the calibrated vocal persona and prosody for Jarvis">
              <div className="grid grid-cols-2 gap-2 w-full max-w-md mt-1">
                {(voiceProfiles.length > 0 ? voiceProfiles : [
                  { id: 'classic_jarvis', name: 'Classic JARVIS', description: 'Sophisticated British male (RyanNeural)' },
                  { id: 'professional', name: 'Professional', description: 'Clear, articulate & objective (ThomasNeural)' },
                  { id: 'friendly', name: 'Friendly', description: 'Warm, conversational & approachable (GuyNeural)' },
                  { id: 'concise', name: 'Concise', description: 'High-tempo & direct responses' },
                ]).map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    onClick={() => handleProfileSelect(profile.id)}
                    className={`flex flex-col text-left p-2.5 rounded-lg border transition-all cursor-pointer ${
                      activeProfile === profile.id
                        ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/10 font-medium'
                        : 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] opacity-80 hover:opacity-100'
                    }`}
                  >
                    <span className="text-xs font-semibold" style={{ color: activeProfile === profile.id ? 'var(--color-accent)' : 'var(--color-text)' }}>
                      {profile.name}
                    </span>
                    <span className="text-[11px] line-clamp-1 mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
                      {profile.description}
                    </span>
                  </button>
                ))}
              </div>
            </SettingRow>

            {/* Voice Dropdown */}
            <SettingRow label="Voice Model" description="Neural voice identity for the active backend">
              <div className="flex items-center gap-2">
                {availableVoices.length > 0 ? (
                  <select
                    value={selectedVoice}
                    onChange={(e) => handleVoiceChange(e.target.value)}
                    className="text-xs px-2.5 py-1.5 rounded-lg border cursor-pointer outline-none"
                    style={{
                      background: 'var(--color-bg-secondary)',
                      borderColor: 'var(--color-border)',
                      color: 'var(--color-text)',
                    }}
                  >
                    {availableVoices.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name} ({v.locale} - {v.backend})
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="text-xs font-mono px-2 py-1 rounded bg-[var(--color-bg-tertiary)]" style={{ color: 'var(--color-text-secondary)' }}>
                    {selectedVoice || ttsBackend?.voice_id || 'en-GB-RyanNeural'}
                  </span>
                )}
              </div>
            </SettingRow>

            {/* Speaking Rate / Speed Slider */}
            <SettingRow label="Speaking Rate" description={`${voiceSpeed.toFixed(2)}x normal speed`}>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min="0.75"
                  max="1.40"
                  step="0.02"
                  value={voiceSpeed}
                  onChange={(e) => handleSpeedChange(parseFloat(e.target.value))}
                  className="w-32 cursor-pointer accent-[var(--color-accent)]"
                />
                <span className="text-xs font-mono w-10 text-right" style={{ color: 'var(--color-text-secondary)' }}>
                  {voiceSpeed.toFixed(2)}x
                </span>
              </div>
            </SettingRow>

            {/* Test Voice Button */}
            <SettingRow label="Preview Voice" description="Listen to the current vocal configuration">
              <button
                type="button"
                onClick={handlePreviewVoice}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                style={{
                  background: isTestingVoice ? 'var(--color-accent)' : 'var(--color-bg-secondary)',
                  color: isTestingVoice ? '#ffffff' : 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                {isTestingVoice ? <StopIcon size={14} /> : <Play size={14} />}
                {isTestingVoice ? 'Stop' : 'Play Sample'}
              </button>
            </SettingRow>

            {/* Backend status badges */}
            <SettingRow label="TTS Engine Status" description="Active text-to-speech synthesis pipeline">
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{
                    background: ttsBackend?.available ? 'var(--color-success)' : 'var(--color-text-tertiary)',
                  }}
                />
                <span className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
                  {ttsBackend === null ? 'Checking...'
                    : ttsBackend.available
                      ? `${ttsBackend.backend?.toUpperCase()} (${ttsBackend.voice_id || selectedVoice})`
                      : 'Unavailable'}
                </span>
              </div>
            </SettingRow>
            <SettingRow label="STT Engine Status" description="Speech recognition microphone pipeline">
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{
                    background: speechBackendAvailable === true ? 'var(--color-success)'
                      : speechBackendAvailable === false ? 'var(--color-text-tertiary)'
                      : 'var(--color-text-tertiary)',
                  }}
                />
                <span className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
                  {speechBackendAvailable === null ? 'Checking...'
                    : speechBackendAvailable ? 'Available'
                    : 'Not configured'}
                </span>
              </div>
            </SettingRow>
            {!speechBackendAvailable && speechBackendAvailable !== null && (
              <div className="text-xs mt-2 px-1" style={{ color: 'var(--color-text-tertiary)' }}>
                Set up a speech backend to use voice input.
                See the <a href="https://github.com/SHIKHIL8137/open-jarvis/blob/main/docs/user-guide/tools.md" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-accent)' }}>documentation</a> for details.
              </div>
            )}
          </Section>

          {/* Data */}
          <Section title="Data">
            <SettingRow label="Conversations" description={`${conversations.length} stored locally`}>
              <div className="flex gap-2">
                <button
                  onClick={handleExport}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                  style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                >
                  <Download size={12} /> Export
                </button>
                <button
                  onClick={handleImport}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                  style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                >
                  <Upload size={12} /> Import
                </button>
              </div>
            </SettingRow>
            <SettingRow label="Clear all data" description="Permanently delete all conversations">
              <button
                onClick={handleClear}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                style={{
                  color: confirmClear ? 'white' : 'var(--color-error)',
                  background: confirmClear ? 'var(--color-error)' : 'transparent',
                  border: '1px solid var(--color-error)',
                }}
                onMouseEnter={(e) => { if (!confirmClear) e.currentTarget.style.background = 'rgba(220,38,38,0.1)'; }}
                onMouseLeave={(e) => { if (!confirmClear) e.currentTarget.style.background = 'transparent'; }}
              >
                <Trash2 size={12} /> {confirmClear ? 'Click again to confirm' : 'Clear'}
              </button>
            </SettingRow>
          </Section>

          {/* Updates */}
          <Section title="Updates">
            <SettingRow label="Auto-update" description="Check for new desktop builds automatically every 30 minutes">
              <button
                onClick={() => handleAutoUpdateToggle(!autoUpdateEnabled)}
                className="relative inline-flex h-5 w-9 items-center rounded-full transition-colors"
                style={{ background: autoUpdateEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}
              >
                <span
                  className="inline-block h-3.5 w-3.5 rounded-full transition-transform"
                  style={{
                    background: 'white',
                    transform: autoUpdateEnabled ? 'translateX(18px)' : 'translateX(2px)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Check for updates" description="Manually check for a new version right now">
              <button
                onClick={handleCheckNow}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
                style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)', color: 'var(--color-text)', cursor: 'pointer' }}
                disabled={updateCheckState === 'checking'}
              >
                <RefreshCw size={12} className={updateCheckState === 'checking' ? 'animate-spin' : ''} />
                {updateCheckState === 'checking' && 'Checking...'}
                {updateCheckState === 'available' && 'Update available — see banner above'}
                {updateCheckState === 'latest' && 'Already up to date'}
                {updateCheckState === 'idle' && 'Check now'}
              </button>
            </SettingRow>
          </Section>

          {/* About */}
          <Section title="About">
            <div className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              <p className="mb-2">
                <span className="font-semibold" style={{ color: 'var(--color-text)' }}>Jarvis</span> — a personal AI assistant that runs on your own PC.
              </p>
              <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                Based on OpenJarvis (Apache-2.0). See NOTICE.
              </p>
              <div className="flex gap-3 mt-3 text-xs">
                <a
                  href="https://github.com/SHIKHIL8137/open-jarvis"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--color-accent)' }}
                >
                  GitHub
                </a>
                <a
                  href="https://github.com/SHIKHIL8137/open-jarvis/tree/main/docs"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--color-accent)' }}
                >
                  Documentation
                </a>
              </div>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
