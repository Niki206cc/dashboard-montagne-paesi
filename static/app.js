const icons = {mail:'✉️',instagram:'📸',shopping:'🛒',fuel:'⛽',cloud:'🌤️',stars:'✨',camera:'📷',message:'💬',calendar:'📅'};
const iconBg = {mail:'#e8effc',instagram:'#fde8f1',shopping:'#fff3e0',fuel:'#fdeceb',cloud:'#e6f3fb',stars:'#f2ecfd',camera:'#eef1f4',message:'#e3f5eb',calendar:'#fff6dc'};
const grid = document.querySelector('#grid');
const template = document.querySelector('#card-template');

function age(date) {
  if (!date) return '';
  const sec = Math.floor((Date.now() - new Date(date)) / 1000);
  if (sec < 60) return 'adesso';
  if (sec < 3600) return `${Math.floor(sec / 60)} min fa`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} ore fa`;
  const g = Math.floor(sec / 86400);
  return g === 1 ? '1 giorno fa' : `${g} giorni fa`;
}

function when(date) {
  if (!date) return '';
  const d = new Date(date);
  if (Number.isNaN(d.valueOf())) return date;
  const today = new Date();
  const yest = new Date(); yest.setDate(today.getDate() - 1);
  const hm = d.toLocaleTimeString('it-IT', {hour:'2-digit', minute:'2-digit'});
  let day;
  if (d.toDateString() === today.toDateString()) day = 'Oggi';
  else if (d.toDateString() === yest.toDateString()) day = 'Ieri';
  else day = d.toLocaleDateString('it-IT', {weekday:'short', day:'2-digit', month:'2-digit'});
  return `${day} alle ${hm} · ${age(date)}`;
}

function activityText(item) {
  if (typeof item === 'string') return item;
  if (item.text) return item.text;
  const parsed = item.date ? new Date(item.date) : null;
  const w = parsed && !Number.isNaN(parsed.valueOf())
    ? parsed.toLocaleString('it-IT', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : item.date || '';
  return [w, item.title, item.from].filter(Boolean).join(' · ');
}

function level(service) {
  if (service.state !== 'running' && service.id !== 'waha') return 'error';
  if (service.health === 'unhealthy') return 'error';
  if (service.summary && service.summary.level) {
    if (service.summary.level === 'error') return 'error';
    if (service.summary.level === 'warn') return 'warn';
  }
  const text = (service.activities || []).map(activityText).join(' ');
  if (service.health === 'starting' || service.unread === -1 || text.includes('🆘')) return 'warn';
  return 'ok';
}
const levelText = {ok:'Operativo', warn:'Da controllare', error:'Problema'};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function render(data) {
  grid.innerHTML = '';
  const chips = document.querySelector('#chips');
  chips.innerHTML = '';
  let problems = 0;

  data.services.forEach(service => {
    const node = template.content.cloneNode(true);
    const card = node.querySelector('.card');
    const lv = level(service);
    if (lv !== 'ok') problems++;
    card.classList.add(lv);
    card.id = `svc-${service.id}`;

    const chip = el('a', `chip ${lv}`); chip.href = `#svc-${service.id}`;
    chip.append(el('i'), document.createTextNode(service.name));
    chips.appendChild(chip);

    const icon = node.querySelector('.icon');
    icon.textContent = icons[service.icon] || '⚙️';
    icon.style.background = iconBg[service.icon] || '#eef4f1';
    node.querySelector('.service-name').textContent = service.name;
    node.querySelector('.description').textContent = service.description;
    node.querySelector('.pill-text').textContent = service.state !== 'running' && service.state !== 'unknown' && service.id !== 'waha'
      ? 'Fermo' : (service.state === 'unknown' && service.id !== 'waha' ? 'Non trovato' : levelText[lv]);

    const titleCount = node.querySelector('.title-count');
    if (service.id === 'comunicati' && service.unread !== null && service.unread >= 0) {
      titleCount.hidden = false; titleCount.textContent = service.unread; titleCount.title = `${service.unread} email nuove`;
    }

    const s = service.summary;
    const kpis = node.querySelector('.kpis');
    const addKpi = (label, value, tone) => {
      const k = el('div', `kpi ${tone || ''}`); k.append(el('span', '', label), el('strong', '', value)); kpis.appendChild(k);
    };
    if (s) (s.kpis || []).forEach(k => addKpi(k.label, String(k.value), k.tone));
    if (service.unread !== null && service.unread !== undefined) addKpi('Email nuove', service.unread < 0 ? 'Errore' : service.unread, service.unread < 0 ? 'bad' : '');

    if (s && s.last) {
      const box = node.querySelector('.last'); box.hidden = false;
      const img = box.querySelector('.last-img');
      if (s.last.image) {
        const probe = new Image(); probe.referrerPolicy = 'no-referrer';
        probe.onload = () => { img.style.backgroundImage = `url("${s.last.image}")`; img.textContent = ''; };
        probe.src = s.last.image;
      }
      img.textContent = icons[service.icon] || '📰';
      box.querySelector('.last-label').textContent = s.last.label || 'Ultimo';
      const t = box.querySelector('.last-title'); t.textContent = s.last.title || '—';
      if (s.last.link) t.href = s.last.link; else t.removeAttribute('href');
      box.querySelector('.last-when').textContent = when(s.last.when);
    }

    if (s && s.queue && s.queue.length) {
      const q = node.querySelector('.queue'); q.hidden = false;
      const ul = q.querySelector('ul');
      s.queue.forEach(x => {
        const li = el('li');
        if (x.image) { const im = el('img'); im.src = x.image; im.loading = 'lazy'; im.referrerPolicy = 'no-referrer'; im.onerror = () => im.replaceWith(el('div', 'ph')); li.appendChild(im); }
        else li.appendChild(el('div', 'ph'));
        li.appendChild(el('span', '', x.title)); ul.appendChild(li);
      });
    }

    if (s && s.errors && s.errors.length) {
      const e = node.querySelector('.errors'); e.hidden = false;
      const ul = e.querySelector('ul');
      s.errors.forEach(x => {
        const li = el('li');
        if (x.when) li.appendChild(el('b', '', new Date(x.when).toLocaleString('it-IT', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})));
        li.appendChild(document.createTextNode(x.text)); ul.appendChild(li);
      });
    }

    const notes = node.querySelector('.notes');
    (s && s.notes || []).filter(Boolean).forEach(n => notes.appendChild(el('li', '', n)));

    if (!s) {
      const log = node.querySelector('.log');
      let acts = service.activities && service.activities.length ? service.activities : service.logs;
      if (service.mail_prefix && service.unread === 0) acts = ['Nessuna nuova email'];
      log.hidden = false;
      (acts && acts.length ? acts : ['Nessuna attività recente']).slice(0, 6).forEach(a => {
        const li = el('li', '', activityText(a)); li.title = li.textContent; log.querySelector('ul').appendChild(li);
      });
    }

    const cs = node.querySelector('.container-state');
    cs.textContent = service.state === 'unknown' ? 'Container non trovato' : `Container ${service.health || service.state}${service.started_at ? ' · avviato ' + age(service.started_at) : ''}`;

    const button = node.querySelector('.button');
    if (service.panel_url) button.href = service.panel_url;
    else { button.classList.add('disabled'); button.textContent = 'Nessun pannello'; }
    grid.appendChild(node);
  });

  document.querySelector('#global-status').textContent = problems ? `${problems} ${problems === 1 ? 'servizio' : 'servizi'} da controllare` : 'Tutto operativo';
  document.querySelector('#global-dot').className = `dot ${problems ? 'error' : ''}`;
  document.querySelector('#updated').textContent = `Aggiornato alle ${new Date(data.updated_at).toLocaleTimeString('it-IT', {hour:'2-digit', minute:'2-digit'})}`;
}

async function load() {
  try {
    const response = await fetch('/api/status', {cache:'no-store'});
    if (!response.ok) throw new Error();
    render(await response.json());
  } catch (error) {
    document.querySelector('#global-status').textContent = 'Dashboard non raggiungibile';
    document.querySelector('#global-dot').className = 'dot error';
  }
}

document.querySelector('#reload').addEventListener('click', load);
load();
setInterval(load, 60000);
