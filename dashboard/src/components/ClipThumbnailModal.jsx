import React, { useEffect, useState } from 'react';
import { AlertCircle, Check, ImagePlus, Loader2 } from 'lucide-react';
import { getApiUrl } from '../config';
import { apiFetch } from '../lib/api';
import Modal from './ui/Modal';
import SegmentedControl from './ui/SegmentedControl';

const DEFAULT_COUNT = 3;
const MAX_TEMPLATE_BYTES = 10 * 1024 * 1024;

const SOURCE_OPTIONS = [
    { value: 'list', label: 'list' },
    { value: 'upload', label: 'upload' },
    { value: 'link', label: 'link' },
];

// Pick one, or tap the picked one again to let YouTube choose.
export function ThumbnailGrid({ thumbnails, selected, onSelect }) {
    return (
        <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
            {thumbnails.map((t) => {
                const isSelected = t.file === selected;
                return (
                    <button
                        key={t.file}
                        type="button"
                        aria-pressed={isSelected}
                        aria-label={isSelected ? 'Thumbnail selected, tap to clear' : 'Select this thumbnail'}
                        onClick={() => onSelect(isSelected ? null : t.file)}
                        className={`relative aspect-[9/16] rounded-input overflow-hidden border-2 transition-colors ${isSelected ? 'border-brass' : 'border-rule hover:border-rule2'}`}
                    >
                        <img src={getApiUrl(t.url)} alt="" loading="lazy" className="w-full h-full object-cover" />
                        {isSelected && (
                            <span className="absolute top-1 right-1 bg-brass text-paper rounded-full p-0.5">
                                <Check size={12} />
                            </span>
                        )}
                    </button>
                );
            })}
        </div>
    );
}

export default function ClipThumbnailModal({ isOpen, onClose, jobId, clipIndex, geminiApiKey, thumbnails, onThumbnails, selected, onSelect }) {
    const [templates, setTemplates] = useState([]);
    const [source, setSource] = useState('list');
    const [template, setTemplate] = useState('auto');
    const [file, setFile] = useState(null);
    const [link, setLink] = useState('');
    const [generating, setGenerating] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!isOpen) return;
        setError('');
        apiFetch(`/api/clip/${jobId}/${clipIndex}/thumbnails`)
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                if (!d) return;
                setTemplates(d.templates || []);
                onThumbnails(d.thumbnails || []);
            })
            .catch(() => {});
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen, jobId, clipIndex]);

    const pickFile = (e) => {
        const picked = e.target.files?.[0] || null;
        if (picked && picked.size > MAX_TEMPLATE_BYTES) {
            setError('the template image is limited to 10 mb.');
            setFile(null);
            return;
        }
        setError('');
        setFile(picked);
    };

    const ready = source === 'list' || (source === 'upload' && file) || (source === 'link' && link.trim());

    const generate = async () => {
        setGenerating(true);
        setError('');
        try {
            const form = new FormData();
            form.append('count', DEFAULT_COUNT);
            if (source === 'list') form.append('template', template);
            if (source === 'upload') form.append('template_file', file);
            if (source === 'link') form.append('template_url', link.trim());
            const res = await apiFetch(`/api/clip/${jobId}/${clipIndex}/thumbnails`, {
                method: 'POST',
                headers: geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {},
                body: form,
            });
            if (!res.ok) {
                const data = await res.json().catch(() => null);
                const detail = data?.detail;
                throw new Error(detail?.message || (typeof detail === 'string' ? detail : `Server error ${res.status}`));
            }
            const data = await res.json();
            onThumbnails(data.thumbnails || []);
            if (data.thumbnails?.length) onSelect(data.thumbnails[0].file);
        } catch (e) {
            setError(e.message);
        } finally {
            setGenerating(false);
        }
    };

    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            eyebrow="CLIP THUMBNAIL"
            title="thumbnail"
            size="md"
            footer={
                <button onClick={generate} disabled={generating || !ready} className="btn-primary w-full">
                    {generating
                        ? <><Loader2 size={16} className="animate-spin" /> generating…</>
                        : <><ImagePlus size={16} /> generate {DEFAULT_COUNT} thumbnails</>}
                </button>
            }
        >
            <div className="space-y-4">
                <div>
                    <label className="eyebrow block mb-2">TEMPLATE</label>
                    <SegmentedControl options={SOURCE_OPTIONS} value={source} onChange={setSource} />
                </div>

                {source === 'list' && (
                    <div role="radiogroup" aria-label="template" className="space-y-1.5">
                        {templates.map((t) => (
                            <button
                                key={t.id}
                                type="button"
                                role="radio"
                                aria-checked={template === t.id}
                                onClick={() => setTemplate(t.id)}
                                className={`w-full text-left px-3 py-2 rounded-input border transition-colors ${template === t.id ? 'border-brass bg-paper3' : 'border-rule hover:bg-paper3'}`}
                            >
                                <span className="block text-sm text-ink2 lowercase">{t.label}</span>
                                <span className="block text-xs text-muted lowercase">{t.hint}</span>
                            </button>
                        ))}
                    </div>
                )}

                {source === 'upload' && (
                    <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        aria-label="template image"
                        onChange={pickFile}
                        className="input-field text-xs"
                    />
                )}

                {source === 'link' && (
                    <input
                        type="url"
                        value={link}
                        onChange={(e) => setLink(e.target.value)}
                        aria-label="template link"
                        placeholder="image, youtube video or page link"
                        className="input-field"
                    />
                )}

                {source !== 'list' && (
                    <p className="text-xs text-muted leading-snug lowercase">
                        Gemini follows its layout and colours, not its people, text or logos. Use a template you are allowed to reference.
                    </p>
                )}

                {error && (
                    <div className="badge-danger">
                        <AlertCircle size={12} className="shrink-0" /> {error}
                    </div>
                )}

                {thumbnails.length > 0 && (
                    <div>
                        <label className="eyebrow block mb-2">PICK ONE TO UPLOAD WITH THE VIDEO</label>
                        <ThumbnailGrid thumbnails={thumbnails} selected={selected} onSelect={onSelect} />
                    </div>
                )}
            </div>
        </Modal>
    );
}
