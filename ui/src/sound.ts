/**
 * The panel's audio output, and the noise a timer makes.
 *
 * One AudioContext for the whole page. Browsers cap how many a document may
 * create, and a second one would also mean a second device clock for the
 * reply scheduler to drift against.
 */

let context: AudioContext | null = null;

export function audioContext(): AudioContext {
  context ??= new AudioContext();
  return context;
}

/**
 * The timer sound, synthesised rather than loaded.
 *
 * Three rising pairs, which is enough to read as an alarm and not as a
 * notification blip. Keeping it as maths avoids shipping an audio file for
 * two seconds of beeping, and it cannot fail to load.
 */
export async function chime(): Promise<void> {
  const audio = audioContext();
  // A context created without a gesture starts suspended. By the time a timer
  // rings the user has almost always clicked or spoken, so this usually
  // succeeds; when it does not, the notification still lands.
  try {
    await audio.resume();
  } catch {
    return;
  }

  const start = audio.currentTime + 0.02;
  for (let pair = 0; pair < 3; pair++) {
    for (const [index, frequency] of [880, 1320].entries()) {
      const at = start + pair * 0.5 + index * 0.16;
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = frequency;
      // Ramped rather than switched: a gain that jumps to full is a click
      // before it is a note.
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.22, at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.15);
      oscillator.connect(gain).connect(audio.destination);
      oscillator.start(at);
      oscillator.stop(at + 0.17);
    }
  }
}

/**
 * Ask for notification permission, once, at a moment it makes sense.
 *
 * Called when a timer is started rather than on load: a prompt that arrives
 * with a reason attached is the one people say yes to, and the panel already
 * asks for the microphone the moment it opens.
 */
export async function ensureNotifications(): Promise<boolean> {
  if (!('Notification' in globalThis)) return false;
  if (Notification.permission === 'granted') return true;
  if (Notification.permission === 'denied') return false;
  try {
    return (await Notification.requestPermission()) === 'granted';
  } catch {
    return false;
  }
}

export function notify(title: string, body: string): void {
  if (!('Notification' in globalThis) || Notification.permission !== 'granted') {
    return;
  }
  try {
    // Tagged so a second timer replaces the first rather than stacking up a
    // column of them. (renotify would make the replacement alert again, but
    // it is specified only for service worker notifications.)
    // Constructing it is what shows it; the handle is of no further use.
    const shown = new Notification(title, { body, tag: 'naka-timer' });
    shown.addEventListener('click', () => {
      globalThis.focus();
      shown.close();
    });
  } catch {
    // Some platforms refuse constructed notifications outside a service
    // worker; the chime and the in-page banner have it covered.
  }
}
