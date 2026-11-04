import React, { useEffect, useState } from 'react';
import { Loader2, FolderPlus, FolderCheck } from 'lucide-react';
import { projectByJob, saveProject } from './api';

// "save to projects" on a finished job. Saving again re-syncs the snapshot
// (e.g. after more subtitle or hook edits) and keeps the same project.
export default function SaveToStudioButton({ jobId, onSaved }) {
  const [project, setProject] = useState(null);
  const [saving, setSaving] = useState(false);
  const [includeSource, setIncludeSource] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    setProject(null);
    setError('');
    if (!jobId) return undefined;
    projectByJob(jobId)
      .then((d) => { if (alive) setProject(d.project || null); })
      .catch(() => {});
    return () => { alive = false; };
  }, [jobId]);

  const handleSave = async () => {
    if (!jobId || saving) return;
    setSaving(true);
    setError('');
    try {
      const d = await saveProject(jobId, { includeSource });
      setProject(d.project);
      onSaved?.(d.project);
    } catch (e) {
      setError(e?.detail || 'could not save this project');
    } finally {
      setSaving(false);
    }
  };

  const saved = !!project;
  return (
    <div className="flex flex-col items-stretch sm:items-end gap-1">
      <div className="flex items-center gap-2">
        <label
          className="readout flex items-center gap-1 cursor-pointer select-none"
          title="Also keep the full source video, so recut and reframe still work after the job expires (uses more disk)"
        >
          <input
            type="checkbox"
            checked={includeSource}
            onChange={(e) => setIncludeSource(e.target.checked)}
            className="accent-current"
          />
          + source
        </label>
        <button
          onClick={handleSave}
          disabled={saving}
          className="btn-ghost px-3 py-2 text-xs"
          title={saved
            ? 'Already saved in Projects. Click to update the saved copy with your latest edits.'
            : 'Keep these clips in Projects. Unsaved jobs are deleted when they expire.'}
        >
          {saving && <><Loader2 size={14} className="animate-spin" />saving…</>}
          {!saving && saved && <><FolderCheck size={14} />saved · update</>}
          {!saving && !saved && <><FolderPlus size={14} />save to projects</>}
        </button>
      </div>
      {error && <p className="text-danger text-xs lowercase">{error}</p>}
    </div>
  );
}
