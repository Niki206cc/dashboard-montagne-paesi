const icons = {mail:'✉',instagram:'◎',shopping:'a',fuel:'⛽',cloud:'☁',stars:'✦',camera:'▣',message:'◉',rss:'◔'};
const grid = document.querySelector('#grid');
const template = document.querySelector('#card-template');

function age(date) {
  if (!date) return '—';
  const sec = Math.floor((Date.now() - new Date(date)) / 1000);
  if (sec < 60) return `${sec}s fa`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min fa`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} ore fa`;
  return `${Math.floor(sec / 86400)} gg fa`;
}

function activityText(item) {
  if (typeof item === 'string') return item;
  if (item.text) return item.text;
  const parsed = item.date ? new Date(item.date) : null;
  const when = parsed && !Number.isNaN(parsed.valueOf())
    ? parsed.toLocaleString('it-IT', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})
    : item.date || '';
  return [when, item.title, item.from].filter(Boolean).join(' · ');
}

function statusLevel(service) {
  const activities = [...(service.activities || []), ...(service.logs || [])]
    .map(activityText).join(' ').toLowerCase();
  if (service.state !== 'running' || service.health === 'unhealthy') return 'red';
  if (service.health === 'starting' || service.panel_online === false || service.unread === -1 || activities.includes('🆘')) return 'orange';
  return 'green';
}

function renderActivity(ul, item) {
  const li = document.createElement('li');
  if (typeof item === 'object' && item.image) {
    li.className = 'activity-with-image';
    const img = document.createElement('img');
    img.src = item.image;
    img.alt = '';
    img.loading = 'lazy';
    img.referrerPolicy = 'no-referrer';
    img.addEventListener('error', () => img.remove());
    li.appendChild(img);
  }
  const text = document.createElement('span');
  text.textContent = activityText(item);
  text.title = text.textContent;
  li.appendChild(text);
  ul.appendChild(li);
}

function render(data) {
  grid.innerHTML = '';
  let problems = 0;
  data.services.forEach(service => {
    const node = template.content.cloneNode(true);
    const card = node.querySelector('.card');
    const level = statusLevel(service);
    if (level !== 'green') problems++;
    if (level === 'red') card.classList.add('error');
    if (level === 'orange') card.classList.add('warning');

    node.querySelector('.icon').textContent = icons[service.icon] || '⚙';
    node.querySelector('.service-name').textContent = service.name;
    node.querySelector('.description').textContent = service.description;

    const traffic = node.querySelector('.traffic-light');
    traffic.classList.add(level);
    traffic.title = level === 'green' ? 'Tutto regolare' : level === 'orange' ? 'Attenzione' : 'Servizio fermo';
    traffic.setAttribute('aria-label', traffic.title);

    const titleCount = node.querySelector('.title-count');
    if (service.id === 'comunicati' && service.unread !== null && service.unread >= 0) {
      titleCount.hidden = false;
      titleCount.textContent = service.unread;
      titleCount.title = `${service.unread} email nuove`;
    }

    node.querySelector('.container-state').textContent = service.health || service.state;
    node.querySelector('.container-state').title = service.started_at ? `Avviato ${age(service.started_at)}` : '';

    const mail = node.querySelector('.mail-box');
    if (service.unread === null) mail.remove();
    else node.querySelector('.unread').textContent = service.unread < 0 ? 'Errore' : service.unread;

    const extra = node.querySelector('.extra-box');
    if (service.id === 'instagram' && service.published_today !== null) {
      extra.hidden = false;
      extra.querySelector('.extra-label').textContent = 'POST PUBBLICATI OGGI';
      extra.querySelector('.extra-value').textContent = service.published_today;
    } else {
      extra.remove();
    }

    const ul = node.querySelector('.log ul');
    let activities = service.activities && service.activities.length ? service.activities : service.logs;
    if (service.mail_prefix && service.unread === 0) activities = ['Nessuna nuova email'];
    (activities.length ? activities : ['Nessuna attività recente']).forEach(item => renderActivity(ul, item));

    const button = node.querySelector('.button');
    if (service.panel_url) button.href = service.panel_url;
    else {
      button.classList.add('disabled');
      button.textContent = 'NESSUN PANNELLO WEB';
    }
    grid.appendChild(node);
  });

  document.querySelector('#global-status').textContent = problems ? `${problems} servizi da controllare` : 'Tutti i servizi operativi';
  document.querySelector('#global-dot').className = `dot ${problems ? 'error' : ''}`;
  document.querySelector('#updated').textContent = `Aggiornato ${new Date(data.updated_at).toLocaleTimeString('it-IT')}`;
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

load();
setInterval(load, 60000);
