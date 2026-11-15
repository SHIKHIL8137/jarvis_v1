import { useEffect, useState, useCallback } from 'react';
import {
  Calendar,
  Clock,
  Play,
  Pause,
  Trash2,
  Moon,
  RefreshCw,
  Plus,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import {
  fetchRoutines,
  pauseRoutine,
  resumeRoutine,
  runRoutineNow,
  deleteRoutine,
  type RoutineItem,
} from '../../lib/api';

export function RoutineManager() {
  const [routines, setRoutines] = useState<RoutineItem[]>([]);
  const [loading, setLoading] = useState(false);

  const loadRoutines = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchRoutines();
      setRoutines(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRoutines();
  }, [loadRoutines]);

  const handlePause = async (id: string) => {
    await pauseRoutine(id);
    loadRoutines();
  };

  const handleResume = async (id: string) => {
    await resumeRoutine(id);
    loadRoutines();
  };

  const handleRunNow = async (id: string) => {
    await runRoutineNow(id);
    loadRoutines();
  };

  const handleDelete = async (id: string) => {
    if (confirm('Delete this scheduled routine?')) {
      await deleteRoutine(id);
      loadRoutines();
    }
  };

  return (
    <div
      className="rounded-xl p-5 mb-6 shadow-sm"
      style={{
        background: 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
      }}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <Calendar size={18} style={{ color: 'var(--color-accent)' }} />
          <h2 className="text-base font-semibold" style={{ color: 'var(--color-text)' }}>
            Unified Routines &amp; Scheduling
          </h2>
          <span
            className="text-xs px-2 py-0.5 rounded-full font-mono"
            style={{
              background: 'var(--color-bg-tertiary)',
              color: 'var(--color-text-secondary)',
            }}
          >
            {routines.length}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Quiet hours indicator */}
          <div
            className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium"
            style={{
              background: 'rgba(99, 102, 241, 0.12)',
              color: '#818cf8',
              border: '1px solid rgba(99, 102, 241, 0.25)',
            }}
            title="Quiet hours configured for Asia/Kolkata (22:00-07:00). Voice output is held silently during quiet hours."
          >
            <Moon size={12} />
            <span>Quiet Hours: Asia/Kolkata</span>
          </div>

          <button
            onClick={loadRoutines}
            disabled={loading}
            className="p-1.5 rounded-lg hover:opacity-80 transition-opacity cursor-pointer disabled:opacity-40"
            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}
            title="Refresh routines"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {routines.length === 0 ? (
        <div className="text-center py-8" style={{ color: 'var(--color-text-tertiary)' }}>
          <Clock size={28} className="mx-auto mb-2 opacity-40" />
          <p className="text-sm">No scheduled routines registered.</p>
          <p className="text-xs mt-1">
            Create recurring reminders, morning briefs, or health checks via Voice or Chat.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {routines.map((rt) => (
            <div
              key={rt.id}
              className="p-4 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-3 transition-all"
              style={{
                background: 'var(--color-bg-tertiary)',
                border: '1px solid var(--color-border)',
                opacity: rt.is_active ? 1 : 0.65,
              }}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full tracking-wider"
                    style={{
                      background: rt.is_active
                        ? 'rgba(34, 197, 94, 0.15)'
                        : 'rgba(156, 163, 175, 0.15)',
                      color: rt.is_active ? '#4ade80' : '#9ca3af',
                    }}
                  >
                    {rt.routine_type}
                  </span>
                  <span className="text-sm font-semibold truncate" style={{ color: 'var(--color-text)' }}>
                    {rt.name}
                  </span>
                </div>
                <p className="text-xs truncate mb-1" style={{ color: 'var(--color-text-secondary)' }}>
                  {rt.description || `Action: ${rt.action_type}`}
                </p>
                <div className="flex items-center gap-3 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
                  {rt.next_run_at && <span>Next: {new Date(rt.next_run_at).toLocaleString()}</span>}
                  {rt.last_status && (
                    <span className="flex items-center gap-1">
                      Status: {rt.last_status}
                    </span>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={() => handleRunNow(rt.id)}
                  className="px-2.5 py-1.5 rounded text-xs font-semibold hover:opacity-80 transition-colors flex items-center gap-1 cursor-pointer"
                  style={{
                    background: 'rgba(59, 130, 246, 0.12)',
                    color: '#3b82f6',
                  }}
                  title="Trigger immediate execution"
                >
                  <Play size={12} />
                  Run
                </button>

                {rt.is_active ? (
                  <button
                    onClick={() => handlePause(rt.id)}
                    className="p-1.5 rounded hover:opacity-80 transition-colors cursor-pointer"
                    style={{
                      background: 'rgba(234, 179, 8, 0.12)',
                      color: '#eab308',
                    }}
                    title="Pause Routine"
                  >
                    <Pause size={14} />
                  </button>
                ) : (
                  <button
                    onClick={() => handleResume(rt.id)}
                    className="p-1.5 rounded hover:opacity-80 transition-colors cursor-pointer"
                    style={{
                      background: 'rgba(34, 197, 94, 0.12)',
                      color: '#22c55e',
                    }}
                    title="Resume Routine"
                  >
                    <Play size={14} />
                  </button>
                )}

                <button
                  onClick={() => handleDelete(rt.id)}
                  className="p-1.5 rounded hover:opacity-80 transition-colors cursor-pointer"
                  style={{
                    background: 'rgba(239, 68, 68, 0.12)',
                    color: '#ef4444',
                  }}
                  title="Delete Routine"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
