// Destinations of a browser post: a saved account and, on YouTube, a channel.

export const targetKey = (t) => `${t.account_id}|${t.channel_id || ''}`;

// The destinations an account offers: one per channel, or, before any channel
// is known, the channel its login happens to be on.
export function accountTargets(account) {
    if (account.channels?.length) {
        return account.channels.map((c) => ({ account_id: account.id, channel_id: c.id, label: c.name }));
    }
    const label = account.platform === 'youtube' ? 'channel its login is on' : 'its logged-in account';
    return [{ account_id: account.id, channel_id: null, label }];
}
