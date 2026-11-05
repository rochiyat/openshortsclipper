import React, { useState } from 'react';
import { ChevronDown, X } from 'lucide-react';

// Resized in the browser so the data URL stays under the server's logo limit
// (video_requirements.MAX_LOGO_BYTES) whatever file is picked.
const LOGO_MAX_SIDE = 512;

function readLogo(file) {
    return new Promise((resolve, reject) => {
        const objectUrl = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const scale = Math.min(1, LOGO_MAX_SIDE / Math.max(img.width, img.height));
            const canvas = document.createElement('canvas');
            canvas.width = Math.max(1, Math.round(img.width * scale));
            canvas.height = Math.max(1, Math.round(img.height * scale));
            canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
            URL.revokeObjectURL(objectUrl);
            resolve(canvas.toDataURL('image/png'));
        };
        img.onerror = () => {
            URL.revokeObjectURL(objectUrl);
            reject(new Error('unreadable image'));
        };
        img.src = objectUrl;
    });
}

export default function VideoRequirementsPanel({ value, onChange }) {
    const [open, setOpen] = useState(false);
    const [logoError, setLogoError] = useState('');
    const set = (key, next) => onChange({ ...value, [key]: next });
    const active = Boolean(value.logo || value.link || value.overlay_text || value.cta_text
        || value.description_text || value.must_include || value.must_avoid);

    const pickLogo = async (e) => {
        const picked = e.target.files && e.target.files[0];
        e.target.value = '';
        if (!picked) return;
        try {
            const logo = await readLogo(picked);
            setLogoError('');
            set('logo', logo);
        } catch {
            setLogoError('That file could not be read. Use a PNG, JPEG or WebP image.');
        }
    };

    return (
        <div className="mt-4">
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className="flex items-center gap-1.5 text-xs text-muted hover:text-ink2 lowercase transition-colors"
            >
                <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
                video requirements
                {active && <span className="text-brass">· applies to this job</span>}
            </button>
            {open && (
                <div className="mt-3 space-y-3 animate-fade">
                    <div>
                        <p className="eyebrow mb-1.5">logo</p>
                        {value.logo ? (
                            <div className="flex flex-wrap items-center gap-2">
                                <img src={value.logo} alt="Selected logo" className="h-10 w-auto max-w-[120px] object-contain rounded border border-rule" />
                                <select
                                    value={value.logo_position || 'top-left'}
                                    onChange={(e) => set('logo_position', e.target.value)}
                                    className="input-field !w-auto text-xs py-1.5"
                                    aria-label="logo position"
                                >
                                    <option value="top-left">Top left</option>
                                    <option value="top-center">Top center</option>
                                    <option value="top-right">Top right</option>
                                </select>
                                <button
                                    type="button"
                                    onClick={() => set('logo', null)}
                                    className="flex items-center gap-1 text-xs text-muted hover:text-ink2 transition-colors"
                                >
                                    <X size={14} /> Remove
                                </button>
                            </div>
                        ) : (
                            <input
                                type="file"
                                accept="image/png,image/jpeg,image/webp"
                                onChange={pickLogo}
                                aria-label="logo file"
                                className="input-field text-xs"
                            />
                        )}
                        {logoError && <p className="mt-1 text-[11px] text-red-500">{logoError}</p>}
                    </div>
                    <div>
                        <p className="eyebrow mb-1.5">link</p>
                        <input
                            type="text"
                            maxLength={200}
                            value={value.link || ''}
                            onChange={(e) => set('link', e.target.value)}
                            placeholder="example.com/offer"
                            className="input-field"
                        />
                        <label className="flex items-center gap-2 mt-1.5 text-xs text-ink2 cursor-pointer select-none">
                            <input
                                type="checkbox"
                                checked={!!value.link_on_video}
                                onChange={(e) => set('link_on_video', e.target.checked)}
                                className="w-4 h-4 shrink-0 accent-[var(--color-accent)] cursor-pointer"
                            />
                            also show it on the video (not clickable)
                        </label>
                    </div>
                    <div>
                        <p className="eyebrow mb-1.5">required text on video</p>
                        <input
                            type="text"
                            maxLength={120}
                            value={value.overlay_text || ''}
                            onChange={(e) => set('overlay_text', e.target.value)}
                            className="input-field"
                        />
                    </div>
                    <div>
                        <p className="eyebrow mb-1.5">call to action at the end</p>
                        <input
                            type="text"
                            maxLength={120}
                            value={value.cta_text || ''}
                            onChange={(e) => set('cta_text', e.target.value)}
                            placeholder="follow for part 2"
                            aria-label="call to action at the end"
                            className="input-field"
                        />
                        <p className="mt-1 text-[11px] text-muted lowercase">
                            shown for the last 4 seconds of every clip. leave empty for none.
                        </p>
                    </div>
                    <div>
                        <p className="eyebrow mb-1.5">required text in description</p>
                        <textarea
                            rows={2}
                            maxLength={500}
                            value={value.description_text || ''}
                            onChange={(e) => set('description_text', e.target.value)}
                            placeholder="#hashtag, disclosure"
                            className="input-field"
                        />
                    </div>
                    <div>
                        <p className="eyebrow mb-1.5">must include</p>
                        <textarea
                            rows={2}
                            maxLength={1000}
                            value={value.must_include || ''}
                            onChange={(e) => set('must_include', e.target.value)}
                            className="input-field"
                        />
                    </div>
                    <div>
                        <p className="eyebrow mb-1.5">must avoid</p>
                        <textarea
                            rows={2}
                            maxLength={1000}
                            value={value.must_avoid || ''}
                            onChange={(e) => set('must_avoid', e.target.value)}
                            className="input-field"
                        />
                    </div>
                    <p className="text-[11px] leading-relaxed text-muted">
                        The logo, link and required text are always added. Must include and
                        must avoid guide the AI&apos;s choice of moments, so check the clips
                        before you post, especially for rules about what is on screen.
                    </p>
                    {active && (
                        <button
                            type="button"
                            onClick={() => onChange({})}
                            className="text-xs text-muted hover:text-ink2 underline underline-offset-2 transition-colors"
                        >
                            Clear all requirements
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}
