/**
 * Copy the logos of the services Naka connects to into public/vendor/brands.
 *
 * From Simple Icons (CC0): one path per logo, drawn in the brand's own colour
 * so a card is recognisable at a glance. The logos themselves remain their
 * owners' trademarks, shown here only to name the service being connected.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as icons from 'simple-icons';

const here = dirname(fileURLToPath(import.meta.url));
const to = join(here, '..', 'public', 'vendor', 'brands');

/** File name the panel asks for, and the Simple Icons export it comes from. */
const BRANDS = {
  telegram: 'siTelegram',
  calendar: 'siGooglecalendar',
  spotify: 'siSpotify',
};

mkdirSync(to, { recursive: true });
for (const [file, name] of Object.entries(BRANDS)) {
  const icon = icons[name];
  if (!icon) throw new Error(`no simple-icons export ${name}`);
  writeFileSync(
    join(to, `${file}.svg`),
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#${icon.hex}">` +
      `<title>${icon.title}</title><path d="${icon.path}"/></svg>\n`,
  );
}

console.log(
  `vendored ${Object.keys(BRANDS).length} brand logos to public/vendor/brands`,
);
