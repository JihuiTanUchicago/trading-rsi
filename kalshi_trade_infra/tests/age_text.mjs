// A relative time a poll compares ("5m ago", "as of 4 min ago") is an age element (core.js ageAt: <time data-age>), and a
// poll whose only change is its words says them again in place (ageTick) instead of writing the region. The offline
// harnesses' elements read their text from the markup they were written with, so a region's textContent keeps the
// words it was drawn with while the age elements inside it hold their new ones. shown(el) is the region's text as a
// reader sees it: its markup with each age element's own current words, tags taken out.
const AGE_EL = /(<time data-age="\w+"[^>]*>)[^<]*(<\/time>)/g;

export function shown(el) {
  const ages = el.querySelectorAll('time[data-age]');
  let i = 0;
  return el.innerHTML.replace(AGE_EL, (m, open, close) => open + (i < ages.length ? ages[i++].textContent : '') + close).replace(/<[^>]*>/g, '');
}

// The markup with each age element's tags taken out and its words kept, for a check written against the words.
export const ageless = (html) => String(html).replace(AGE_EL, (m) => m.replace(/<[^>]*>/g, ''));
