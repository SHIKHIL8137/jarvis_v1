import { useEffect, useState, useCallback } from 'react';
import {
  Play,
  Pause,
  XOctagon,
  Navigation,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Clock,
  Layers,
} from 'lucide-react';
import {
  fetchBackgroundTasks,
  pauseBackgroundTask,
  resumeBackgroundTask,
  cancelBackgroundTask,
  steerBackgroundTask,
  type BackgroundTask,
} from '../../lib/api';

const STATUS_COLORS: Record<string, { bg: string; text: string }> = {
  running: { bg: 'rgba(59, 130, 246, 0.15)', text: '#60a5fa' },
  planning: { bg: 'rgba(168, 85, 247, 0.15)', text: '#c084fc' },
  waiting_approval: { bg: 'rgba(234, 179, 8, 0.15)', text: '#facc15' },
  paused: { bg: 'rgba(156, 163, 175, 0.15)', text: '#9ca3af' },
  recovering: { bg: 'rgba(249, 115, 22, 0.15)', text: '#fb923c' },
  verifying: { bg: 'rgba(14, 165, 233, 0.15)', text: '#38bdf8' },
  completed: { bg: 'rgba(34, 197, 94, 0.15)', text: '#4ade80' },
  failed: { bg: 'rgba(239, 68, 68, 0.15)', text: '#f87171' },
  cancelled: { bg: 'rgba(107, 114, 128, 0.15)', text: '#9ca3af' },
};

export function TaskCenter() {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<string>('all');
  const [steerTaskId, setSteerTaskId] = useState<string | null>(null);
  const [steerText, setSteerText] = useState('');
  const [steerSubmitting, setSteerSubmitting] = useState(false);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchBackgroundTasks(filter === 'all' ? undefined : filter);
      setTasks(data);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadTasks();
    const interval = setInterval(loadTasks, 4000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  const handlePause = async (taskId: string) => {
    await pauseBackgroundTask(taskId);
    loadTasks();
  };

  const handleResume = async (taskId: string) => {
    await resumeBackgroundTask(taskId);
    loadTasks();
  };

  const handleCancel = async (taskId: string) => {
    if (confirm('Are you sure you want to cancel this background task?')) {
      await cancelBackgroundTask(taskId);
      loadTasks();
    }
  };

  const handleSteerSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!steerTaskId || !steerText.trim()) return;
    setSteerSubmitting(true);
    try {
      await steerBackgroundTask(steerTaskId, steerText.trim());
      setSteerTaskId(null);
      setSteerText('');
      loadTasks();
    } finally {
      setSteerSubmitting(false);
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
          <Layers size={18} style={{ color: 'var(--color-accent)' }} />
          <h2 className="text-base font-semibold" style={{ color: 'var(--color-text)' }}>
            Autonomous Background Tasks
          </h2>
          <span
            className="text-xs px-2 py-0.5 rounded-full font-mono"
            style={{
              background: 'var(--color-bg-tertiary)',
              color: 'var(--color-text-secondary)',
            }}
          >
            {tasks.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Status filters */}
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="text-xs px-2.5 py-1.5 rounded-lg font-medium cursor-pointer"
            style={{
              background: 'var(--color-bg-tertiary)',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
            }}
          >
            <option value="all">All States</option>
            <option value="running">Running</option>
            <option value="waiting_approval">Waiting Approval</option>
            <option value="paused">Paused</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
          <button
            onClick={loadTasks}
            disabled={loading}
            className="p-1.5 rounded-lg hover:opacity-80 transition-opacity cursor-pointer disabled:opacity-40"
            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}
            title="Refresh tasks"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {tasks.length === 0 ? (
        <div className="text-center py-8" style={{ color: 'var(--color-text-tertiary)' }}>
          <Clock size={28} className="mx-auto mb-2 opacity-40" />
          <p className="text-sm">No autonomous tasks in this view.</p>
          <p className="text-xs mt-1">
            Delegate a research or coding goal by asking Jarvis in Chat or Voice.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map((task) => {
            const stColor = STATUS_COLORS[task.status] || {
              bg: 'rgba(156,163,175,0.15)',
              text: '#9ca3af',
            };
            const isActive = task.status === 'running' || task.status === 'planning';
            const isPaused = task.status === 'paused';

            return (
              <div
                key={task.task_id}
                className="p-4 rounded-lg flex flex-col gap-2.5 transition-all"
                style={{
                  background: 'var(--color-bg-tertiary)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className="text-[11px] font-semibold uppercase px-2 py-0.5 rounded-full tracking-wider"
                        style={{ background: stColor.bg, color: stColor.text }}
                      >
                        {task.status.replace('_', ' ')}
                      </span>
                      <span
                        className="text-[11px] font-mono px-2 py-0.5 rounded"
                        style={{
                          background: 'var(--color-bg-secondary)',
                          color: 'var(--color-text-tertiary)',
                        }}
                      >
                        profile: {task.capability_profile}
                      </span>
                    </div>
                    <p
                      className="text-sm font-medium leading-relaxed truncate"
                      style={{ color: 'var(--color-text)' }}
                      title={task.goal}
                    >
                      {task.goal}
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1.5 shrink-0">
                    {isActive && (
                      <button
                        onClick={() => handlePause(task.task_id)}
                        className="p-1.5 rounded hover:opacity-80 transition-colors"
                        style={{
                          background: 'rgba(234, 179, 8, 0.12)',
                          color: '#eab308',
                        }}
                        title="Pause Task"
                      >
                        <Pause size={14} />
                      </button>
                    )}

                    {isPaused && (
                      <button
                        onClick={() => handleResume(task.task_id)}
                        className="p-1.5 rounded hover:opacity-80 transition-colors"
                        style={{
                          background: 'rgba(34, 197, 94, 0.12)',
                          color: '#22c55e',
                        }}
                        title="Resume Task"
                      >
                        <Play size={14} />
                      </button>
                    )}

                    {(isActive || isPaused) && (
                      <>
                        <button
                          onClick={() => {
                            setSteerTaskId(task.task_id);
                            setSteerText('');
                          }}
                          className="p-1.5 rounded hover:opacity-80 transition-colors"
                          style={{
                            background: 'rgba(59, 130, 246, 0.12)',
                            color: '#3b82f6',
                          }}
                          title="Steer / Inject Guidance"
                        >
                          <Navigation size={14} />
                        </button>

                        <button
                          onClick={() => handleCancel(task.task_id)}
                          className="p-1.5 rounded hover:opacity-80 transition-colors"
                          style={{
                            background: 'rgba(239, 68, 68, 0.12)',
                            color: '#ef4444',
                          }}
                          title="Cancel Task"
                        >
                          <XOctagon size={14} />
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {/* Progress bar */}
                <div>
                  <div className="flex justify-between text-[11px] mb-1" style={{ color: 'var(--color-text-secondary)' }}>
                    <span>
                      Step {task.current_step_index} of {task.total_steps || 1}
                    </span>
                    <span>{task.progress_pct}%</span>
                  </div>
                  <div
                    className="w-full h-1.5 rounded-full overflow-hidden"
                    style={{ background: 'var(--color-border)' }}
                  >
                    <div
                      className="h-full rounded-full transition-all duration-300"
                      style={{
                        width: `${task.progress_pct}%`,
                        background:
                          task.status === 'failed'
                            ? 'var(--color-error)'
                            : task.status === 'completed'
                            ? 'var(--color-success)'
                            : 'var(--color-accent)',
                      }}
                    />
                  </div>
                </div>

                {task.error && (
                  <div
                    className="text-xs p-2 rounded flex items-center gap-1.5"
                    style={{ background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444' }}
                  >
                    <AlertCircle size={13} className="shrink-0" />
                    <span className="truncate">{task.error}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Steer Modal */}
      {steerTaskId && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-xs flex items-center justify-center z-50 p-4">
          <form
            onSubmit={handleSteerSubmit}
            className="w-full max-w-md p-5 rounded-xl shadow-xl flex flex-col gap-3"
            style={{
              background: 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
            }}
          >
            <div className="flex items-center gap-2">
              <Navigation size={16} style={{ color: 'var(--color-accent)' }} />
              <h3 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
                Steer Background Task
              </h3>
            </div>
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              Inject steering guidance into the task loop. Jarvis will integrate this prompt into the next execution step.
            </p>
            <textarea
              value={steerText}
              onChange={(e) => setSteerText(e.target.value)}
              placeholder="e.g. Focus on benchmarks for GTX 1650 instead of 3090, or skip step 2..."
              rows={3}
              required
              className="w-full p-2.5 rounded-lg text-xs outline-none"
              style={{
                background: 'var(--color-bg-tertiary)',
                color: 'var(--color-text)',
                border: '1px solid var(--color-border)',
              }}
            />
            <div className="flex justify-end gap-2 mt-1">
              <button
                type="button"
                onClick={() => setSteerTaskId(null)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer"
                style={{
                  background: 'var(--color-bg-tertiary)',
                  color: 'var(--color-text-secondary)',
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={steerSubmitting || !steerText.trim()}
                className="px-3.5 py-1.5 rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-40"
                style={{
                  background: 'var(--color-accent)',
                  color: '#ffffff',
                }}
              >
                {steerSubmitting ? 'Sending...' : 'Send Guidance'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
