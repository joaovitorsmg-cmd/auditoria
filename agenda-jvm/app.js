/* ============================================================
   PAINEL JVM — app.js
   Estado local + JSONBin sync + Google Calendar OAuth
   ============================================================ */

const STORAGE_KEY = 'painel_jvm_state_v1';
const CONFIG_KEY = 'painel_jvm_config_v1';

const DEFAULT_RECURRENTES = [
  { id: 'r1', nome: 'Envio relatório auditoria filiais', freq: 'semanal', diaRef: 5, ultimoFeito: null },
  { id: 'r2', nome: 'Verificação banco de horas', freq: 'quinzenal', diaRef: 1, ultimoFeito: null },
  { id: 'r3', nome: 'Revisão licenças/alvarás vencendo', freq: 'mensal', diaRef: 1, ultimoFeito: null },
  { id: 'r4', nome: 'Conferência notas fiscais pendentes', freq: 'semanal', diaRef: 4, ultimoFeito: null },
  { id: 'r5', nome: 'Atualização painel compras (Kataki)', freq: 'quinzenal', diaRef: 1, ultimoFeito: null },
  { id: 'r6', nome: 'Backup/sync planilhas auditoria', freq: 'semanal', diaRef: 5, ultimoFeito: null },
  { id: 'r7', nome: 'Revisão robo_auditoria.py logs', freq: 'quinzenal', diaRef: 3, ultimoFeito: null }
];

function defaultState(){
  return {
    recorrentes: DEFAULT_RECURRENTES,
    notas: [],
    demandas: [],
    eventosLocais: [],
    lastOpenState: null,
    lastActivity: null
  };
}

function loadState(){
  try{
    const raw = localStorage.getItem(STORAGE_KEY);
    if(!raw) return defaultState();
    const parsed = JSON.parse(raw);
    return Object.assign(defaultState(), parsed);
  }catch(e){
    console.error('Erro ao carregar estado', e);
    return defaultState();
  }
}

function saveState(){
  STATE.lastActivity = new Date().toISOString();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(STATE));
  syncToCloudDebounced();
}

function loadConfig(){
  try{
    const raw = localStorage.getItem(CONFIG_KEY);
    return raw ? JSON.parse(raw) : { googleClientId:'', binId:'', binKey:'', googleToken:null, googleTokenExp:null };
  }catch(e){
    return { googleClientId:'', binId:'', binKey:'', googleToken:null, googleTokenExp:null };
  }
}

function saveConfig(){
  localStorage.setItem(CONFIG_KEY, JSON.stringify(CONFIG));
}

let STATE = loadState();
let CONFIG = loadConfig();

/* ============================================================
   UTILIDADES
   ============================================================ */

function uid(){
  return Math.random().toString(36).slice(2,10) + Date.now().toString(36);
}

function showToast(msg, ms=2600){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(()=>t.classList.remove('show'), ms);
}

function extractTags(text){
  const matches = text.match(/#[\wÀ-ÿ]+/g);
  return matches ? matches.map(t=>t.toLowerCase()) : [];
}

function formatDatePt(date){
  return date.toLocaleDateString('pt-BR', { weekday:'long', day:'2-digit', month:'long' });
}

function isSameDay(d1, d2){
  return d1.getFullYear()===d2.getFullYear() && d1.getMonth()===d2.getMonth() && d1.getDate()===d2.getDate();
}

function daysBetween(d1, d2){
  const ms = new Date(d2.getFullYear(),d2.getMonth(),d2.getDate()) - new Date(d1.getFullYear(),d1.getMonth(),d1.getDate());
  return Math.round(ms / 86400000);
}

/* ============================================================
   RELÓGIO E DATA
   ============================================================ */

function tickClock(){
  const now = new Date();
  document.getElementById('clockLive').textContent = now.toLocaleTimeString('pt-BR');
  document.getElementById('dateLine').textContent = formatDatePt(now);
}
setInterval(tickClock, 1000);
tickClock();

/* ============================================================
   STATUS DE RECORRÊNCIA
   ============================================================ */

function getRecurStatus(item){
  const hoje = new Date();
  if(!item.ultimoFeito){
    return 'atrasado';
  }
  const ultimo = new Date(item.ultimoFeito);
  const dias = daysBetween(ultimo, hoje);
  const limites = { semanal: 7, quinzenal: 15, mensal: 30 };
  const limite = limites[item.freq] || 7;

  if(dias >= limite) return 'atrasado';
  if(dias >= limite - 2) return 'proximo';
  return 'ok';
}

function marcarFeito(id){
  const item = STATE.recorrentes.find(r=>r.id===id);
  if(!item) return;
  item.ultimoFeito = new Date().toISOString();
  saveState();
  renderRecorrentes();
  renderSummary();
  showToast(`"${item.nome}" marcada como feita`);
}

function adiarRecorrente(id){
  const item = STATE.recorrentes.find(r=>r.id===id);
  if(!item) return;
  // adia empurrando "ultimoFeito" 1 dia pra frente artificialmente (reduz urgência sem contar como feito)
  const base = item.ultimoFeito ? new Date(item.ultimoFeito) : new Date();
  base.setDate(base.getDate()+1);
  item.ultimoFeito = base.toISOString();
  saveState();
  renderRecorrentes();
  showToast(`"${item.nome}" adiada`);
}

/* ============================================================
   RESUMO AUTOMÁTICO
   ============================================================ */

function renderSummary(){
  const atrasadas = STATE.recorrentes.filter(r=>getRecurStatus(r)==='atrasado').length;
  const eventosHoje = STATE.eventosLocais.filter(e=>isSameDay(new Date(e.start), new Date())).length;
  const demandasAbertas = STATE.demandas.filter(d=>!d.concluida).length;

  const partes = [];
  if(atrasadas>0) partes.push(`${atrasadas} recorrente${atrasadas>1?'s':''} atrasada${atrasadas>1?'s':''}`);
  if(eventosHoje>0) partes.push(`${eventosHoje} evento${eventosHoje>1?'s':''} hoje`);
  if(demandasAbertas>0) partes.push(`${demandasAbertas} demanda${demandasAbertas>1?'s':''} em aberto`);

  const texto = partes.length ? partes.join(' · ') : 'Tudo em dia. Nenhuma pendência no momento.';
  document.getElementById('summaryText').textContent = texto;

  const badge = document.getElementById('pendingBadge');
  const total = atrasadas + demandasAbertas;
  if(total>0){
    badge.textContent = total;
    badge.classList.add('show');
    document.title = `(${total}) Painel JVM`;
  }else{
    badge.classList.remove('show');
    document.title = 'Painel JVM';
  }
}

/* ============================================================
   RENDER: RECORRENTES (preview + modal completo)
   ============================================================ */

const FREQ_LABEL = { semanal:'semanal', quinzenal:'quinzenal', mensal:'mensal' };

function renderRecorrentes(){
  const counts = { semanal:0, quinzenal:0, mensal:0 };
  STATE.recorrentes.forEach(r=>counts[r.freq]++);
  document.getElementById('countSemanal').textContent = counts.semanal;
  document.getElementById('countQuinzenal').textContent = counts.quinzenal;
  document.getElementById('countMensal').textContent = counts.mensal;

  // preview: mostra até 4, priorizando atrasadas/próximas
  const ordenadas = [...STATE.recorrentes].sort((a,b)=>{
    const ordem = { atrasado:0, proximo:1, ok:2 };
    return ordem[getRecurStatus(a)] - ordem[getRecurStatus(b)];
  });

  const previewEl = document.getElementById('recurPreviewList');
  previewEl.innerHTML = ordenadas.slice(0,4).map(r=>{
    const status = getRecurStatus(r);
    return `
      <div class="recur-preview-item">
        <span class="status-dot ${status}"></span>
        <span class="recur-preview-name">${r.nome}</span>
        <span class="recur-preview-freq">${FREQ_LABEL[r.freq]}</span>
      </div>`;
  }).join('') || '<div class="empty-state">Nenhuma recorrente cadastrada.</div>';

  renderRecurFullList();
}

function renderRecurFullList(){
  const el = document.getElementById('recurFullList');
  if(!STATE.recorrentes.length){
    el.innerHTML = '<div class="empty-state">Nenhuma recorrente cadastrada ainda.</div>';
    return;
  }
  el.innerHTML = STATE.recorrentes.map(r=>{
    const status = getRecurStatus(r);
    const ultimo = r.ultimoFeito ? new Date(r.ultimoFeito).toLocaleDateString('pt-BR') : 'nunca feito';
    return `
      <div class="recur-preview-item" style="align-items:flex-start;padding:10px 0;">
        <span class="status-dot ${status}" style="margin-top:4px;"></span>
        <div style="flex:1;">
          <div class="recur-preview-name" style="font-weight:600;">${r.nome}</div>
          <div class="recur-preview-freq">${FREQ_LABEL[r.freq]} · último: ${ultimo}</div>
        </div>
        <div style="display:flex;gap:4px;">
          <button class="note-icon-btn" title="Marcar feito" onclick="marcarFeito('${r.id}')">✓</button>
          <button class="note-icon-btn" title="Adiar" onclick="adiarRecorrente('${r.id}')">⏭</button>
          <button class="note-icon-btn" title="Excluir" onclick="excluirRecorrente('${r.id}')">✕</button>
        </div>
      </div>`;
  }).join('');
}

function excluirRecorrente(id){
  STATE.recorrentes = STATE.recorrentes.filter(r=>r.id!==id);
  saveState();
  renderRecorrentes();
  renderSummary();
  showToast('Recorrente removida');
}

function adicionarRecorrente(nome, freq){
  STATE.recorrentes.push({ id:uid(), nome, freq, diaRef:1, ultimoFeito:null });
  saveState();
  renderRecorrentes();
  renderSummary();
}

/* ============================================================
   RENDER: ANOTAÇÕES
   ============================================================ */

function renderNotes(filterText=''){
  const el = document.getElementById('notesList');
  const term = filterText.trim().toLowerCase();
  let lista = [...STATE.notas].sort((a,b)=> new Date(b.criadoEm) - new Date(a.criadoEm));

  if(term){
    lista = lista.filter(n =>
      n.texto.toLowerCase().includes(term) ||
      n.tags.some(t=>t.includes(term))
    );
  }

  document.getElementById('notesCount').textContent = `${STATE.notas.length} nota${STATE.notas.length!==1?'s':''}`;

  if(!lista.length){
    el.innerHTML = `<div class="empty-state">${term ? 'Nada encontrado.' : 'Nenhuma anotação ainda.'}</div>`;
    return;
  }

  el.innerHTML = lista.map(n=>`
    <div class="note-item">
      <div class="note-text">${escapeHtml(n.texto)}</div>
      <div class="note-meta">
        <div class="note-tags">${n.tags.map(t=>`<span class="note-tag">${t}</span>`).join('')}</div>
        <div class="note-actions">
          <button class="note-icon-btn" title="Transformar em demanda" onclick="notaParaDemanda('${n.id}')">→</button>
          <button class="note-icon-btn" title="Agendar no Google" onclick="notaParaEvento('${n.id}')">📅</button>
          <button class="note-icon-btn" title="Excluir" onclick="excluirNota('${n.id}')">✕</button>
        </div>
      </div>
    </div>`).join('');
}

function escapeHtml(str){
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function salvarNota(){
  const input = document.getElementById('noteInput');
  const texto = input.value.trim();
  if(!texto) return;
  STATE.notas.push({ id:uid(), texto, tags:extractTags(texto), criadoEm:new Date().toISOString() });
  input.value = '';
  saveState();
  renderNotes(document.getElementById('noteSearch').value);
  showToast('Anotação salva');
}

function excluirNota(id){
  STATE.notas = STATE.notas.filter(n=>n.id!==id);
  saveState();
  renderNotes(document.getElementById('noteSearch').value);
}

function notaParaDemanda(id){
  const nota = STATE.notas.find(n=>n.id===id);
  if(!nota) return;
  STATE.demandas.push({ id:uid(), titulo:nota.texto, prioridade:'media', concluida:false, criadoEm:new Date().toISOString() });
  saveState();
  renderDemands();
  renderSummary();
  showToast('Nota convertida em demanda');
}

function notaParaEvento(id){
  const nota = STATE.notas.find(n=>n.id===id);
  if(!nota) return;
  openEventModal(nota.texto);
}

/* ============================================================
   RENDER: DEMANDAS
   ============================================================ */

let demandFilterAtivo = 'todas';

function renderDemands(){
  const el = document.getElementById('demandsGrid');
  let lista = STATE.demandas.filter(d=>!d.concluida);

  if(demandFilterAtivo !== 'todas'){
    lista = lista.filter(d=>d.prioridade === demandFilterAtivo);
  }

  const ordem = { alta:0, media:1, baixa:2 };
  lista.sort((a,b)=>ordem[a.prioridade]-ordem[b.prioridade]);

  if(!lista.length){
    el.innerHTML = '<div class="empty-state">Nenhuma demanda em aberto.</div>';
    return;
  }

  el.innerHTML = lista.map(d=>`
    <div class="demand-card ${d.prioridade}">
      <div class="demand-title">${escapeHtml(d.titulo)}</div>
      <div class="demand-meta">
        <span>${d.prioridade}</span>
        <span>${new Date(d.criadoEm).toLocaleDateString('pt-BR')}</span>
      </div>
      <div class="demand-actions">
        <button onclick="concluirDemanda('${d.id}')">concluir</button>
        <button onclick="agendarDemanda('${d.id}')">agendar</button>
      </div>
    </div>`).join('');
}

function concluirDemanda(id){
  const d = STATE.demandas.find(x=>x.id===id);
  if(!d) return;
  d.concluida = true;
  d.concluidoEm = new Date().toISOString();
  saveState();
  renderDemands();
  renderSummary();
  showToast('Demanda concluída');
}

function agendarDemanda(id){
  const d = STATE.demandas.find(x=>x.id===id);
  if(!d) return;
  openEventModal(d.titulo);
}

function autoArquivarDemandas(){
  const LIMITE_DIAS = 7;
  const hoje = new Date();
  STATE.demandas = STATE.demandas.filter(d=>{
    if(!d.concluida) return true;
    const dias = daysBetween(new Date(d.concluidoEm), hoje);
    return dias < LIMITE_DIAS;
  });
}

/* ============================================================
   GOOGLE CALENDAR — OAuth client-side (implicit flow) + API
   ============================================================ */

const GOOGLE_SCOPE = 'https://www.googleapis.com/auth/calendar.events';
const COR_EVENTO_PAINEL = '9'; // "Blueberry" no Google Calendar — usado para eventos criados por aqui

function isGoogleConnected(){
  return CONFIG.googleToken && CONFIG.googleTokenExp && Date.now() < CONFIG.googleTokenExp;
}

function updateGoogleStatusUI(){
  const el = document.getElementById('googleStatus');
  const btnHeader = document.getElementById('btnConnectGoogle');
  if(isGoogleConnected()){
    el.textContent = 'conectado';
    el.classList.add('connected');
    btnHeader.textContent = 'sincronizado';
  }else{
    el.textContent = 'desconectado';
    el.classList.remove('connected');
    btnHeader.textContent = 'conectar';
  }
}

function iniciarGoogleAuth(){
  const clientId = document.getElementById('cfgGoogleClientId').value.trim() || CONFIG.googleClientId;
  if(!clientId){
    showToast('Informe o Client ID do Google primeiro');
    return;
  }
  CONFIG.googleClientId = clientId;
  saveConfig();

  const redirectUri = window.location.origin + window.location.pathname;
  const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?` +
    `client_id=${encodeURIComponent(clientId)}` +
    `&redirect_uri=${encodeURIComponent(redirectUri)}` +
    `&response_type=token` +
    `&scope=${encodeURIComponent(GOOGLE_SCOPE)}` +
    `&include_granted_scopes=true`;

  window.location.href = authUrl;
}

function capturarTokenDaURL(){
  if(window.location.hash.includes('access_token')){
    const params = new URLSearchParams(window.location.hash.substring(1));
    const token = params.get('access_token');
    const expiresIn = parseInt(params.get('expires_in') || '3600', 10);
    if(token){
      CONFIG.googleToken = token;
      CONFIG.googleTokenExp = Date.now() + (expiresIn*1000);
      saveConfig();
      history.replaceState(null, '', window.location.pathname);
      showToast('Google Agenda conectado');
    }
  }
}

async function googleApiFetch(path, options={}){
  if(!isGoogleConnected()){
    showToast('Google Agenda não conectado');
    return null;
  }
  const resp = await fetch(`https://www.googleapis.com/calendar/v3${path}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${CONFIG.googleToken}`,
      'Content-Type': 'application/json',
      ...(options.headers||{})
    }
  });
  if(resp.status===401){
    CONFIG.googleToken = null;
    saveConfig();
    updateGoogleStatusUI();
    showToast('Sessão do Google expirou, reconecte');
    return null;
  }
  return resp.json();
}

async function carregarEventosGoogle(){
  if(!isGoogleConnected()){
    return;
  }
  const agora = new Date();
  const em7dias = new Date();
  em7dias.setDate(agora.getDate()+7);

  const data = await googleApiFetch(
    `/calendars/primary/events?timeMin=${agora.toISOString()}&timeMax=${em7dias.toISOString()}&singleEvents=true&orderBy=startTime&maxResults=30`
  );

  if(!data || !data.items) return;

  STATE.eventosLocais = data.items.map(ev=>({
    id: ev.id,
    titulo: ev.summary || '(sem título)',
    start: ev.start.dateTime || ev.start.date,
    fromPainel: ev.colorId === COR_EVENTO_PAINEL
  }));
  saveState();
  renderEventos();
  renderSummary();
}

function renderEventos(){
  const el = document.getElementById('eventsList');
  if(!isGoogleConnected()){
    el.innerHTML = '<div class="empty-state">Conecte o Google Agenda para ver seus eventos aqui.</div>';
    return;
  }
  if(!STATE.eventosLocais.length){
    el.innerHTML = '<div class="empty-state">Nenhum evento nos próximos 7 dias.</div>';
    return;
  }
  el.innerHTML = STATE.eventosLocais.map(ev=>{
    const d = new Date(ev.start);
    const hora = ev.start.includes('T') ? d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'}) : 'dia todo';
    return `
      <div class="event-item">
        <span class="event-time">${hora}</span>
        <span class="event-source-dot" style="background:${ev.fromPainel?'var(--azul-royal)':'var(--cinza-claro)'}"></span>
        <span class="event-title">${escapeHtml(ev.titulo)}</span>
      </div>`;
  }).join('');
}

async function criarEventoGoogle(titulo, dataISO){
  if(!isGoogleConnected()){
    showToast('Conecte o Google Agenda nas configurações primeiro');
    return;
  }

  // checagem simples de conflito
  const conflito = STATE.eventosLocais.find(ev=>{
    const diff = Math.abs(new Date(ev.start) - new Date(dataISO));
    return diff < 30*60000; // 30 min de tolerância
  });
  if(conflito && !confirm(`Já existe "${conflito.titulo}" próximo desse horário. Criar mesmo assim?`)){
    return;
  }

  const start = new Date(dataISO);
  const end = new Date(start.getTime() + 60*60000); // +1h padrão

  const body = {
    summary: titulo,
    start: { dateTime: start.toISOString() },
    end: { dateTime: end.toISOString() },
    colorId: COR_EVENTO_PAINEL
  };

  const data = await googleApiFetch('/calendars/primary/events', {
    method:'POST',
    body: JSON.stringify(body)
  });

  if(data && data.id){
    showToast('Evento criado no Google Agenda');
    carregarEventosGoogle();
  }
}

/* ============================================================
   PARSER DE COMANDO RÁPIDO (texto natural pt-BR → data/hora)
   ============================================================ */

const DIAS_SEMANA = ['domingo','segunda','terça','quarta','quinta','sexta','sábado'];

function parseComandoRapido(texto){
  const lower = texto.toLowerCase();
  const hoje = new Date();
  let dataAlvo = null;

  // dia da semana
  for(let i=0;i<DIAS_SEMANA.length;i++){
    if(lower.includes(DIAS_SEMANA[i])){
      dataAlvo = new Date(hoje);
      let diff = (i - hoje.getDay() + 7) % 7;
      if(diff===0) diff = 7;
      dataAlvo.setDate(hoje.getDate()+diff);
      break;
    }
  }
  if(!dataAlvo && lower.includes('amanhã')){
    dataAlvo = new Date(hoje);
    dataAlvo.setDate(hoje.getDate()+1);
  }
  if(!dataAlvo && lower.includes('hoje')){
    dataAlvo = new Date(hoje);
  }
  if(!dataAlvo) return null;

  // hora tipo "14h" "14:30" "9h"
  const horaMatch = lower.match(/(\d{1,2})[h:](\d{2})?/);
  if(horaMatch){
    dataAlvo.setHours(parseInt(horaMatch[1],10), parseInt(horaMatch[2]||'0',10), 0, 0);
  }else{
    dataAlvo.setHours(9,0,0,0);
  }

  return dataAlvo;
}

/* ============================================================
   JSONBIN — sincronização em nuvem (debounced)
   ============================================================ */

// Cria um Bin novo automaticamente a partir só da Master Key — o usuário
// não precisa saber o que é um "Bin ID" nem criar nada manualmente em
// jsonbin.io: digita a chave e o painel resolve o resto sozinho.
async function criarBinAutomatico(masterKey){
  const r = await fetch('https://api.jsonbin.io/v3/b', {
    method:'POST',
    headers:{
      'Content-Type':'application/json',
      'X-Master-Key': masterKey,
      'X-Bin-Name': 'Painel-JVM',
      'X-Bin-Private': 'true'
    },
    body: JSON.stringify(STATE)
  });
  const d = await r.json();
  if(!r.ok) throw new Error(d.message || 'Master Key inválida ou sem permissão.');
  return d.metadata.id;
}

let syncTimer = null;

function syncToCloudDebounced(){
  if(!CONFIG.binId || !CONFIG.binKey) return;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(syncToCloud, 1500);
}

async function syncToCloud(){
  if(!CONFIG.binId || !CONFIG.binKey) return;
  try{
    await fetch(`https://api.jsonbin.io/v3/b/${CONFIG.binId}`, {
      method:'PUT',
      headers:{
        'Content-Type':'application/json',
        'X-Master-Key': CONFIG.binKey
      },
      body: JSON.stringify(STATE)
    });
    document.getElementById('lastSyncLine').textContent = `último sync: ${new Date().toLocaleTimeString('pt-BR')}`;
  }catch(e){
    console.error('Erro ao sincronizar JSONBin', e);
  }
}

async function loadFromCloud(){
  if(!CONFIG.binId || !CONFIG.binKey) return;
  try{
    const resp = await fetch(`https://api.jsonbin.io/v3/b/${CONFIG.binId}/latest`, {
      headers:{ 'X-Master-Key': CONFIG.binKey }
    });
    const data = await resp.json();
    if(data && data.record){
      STATE = Object.assign(defaultState(), data.record);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(STATE));
      renderAll();
      showToast('Dados sincronizados da nuvem');
    }
  }catch(e){
    console.error('Erro ao carregar do JSONBin', e);
  }
}

/* ============================================================
   MODAIS — abrir/fechar
   ============================================================ */

function openModal(id){
  document.getElementById(id).classList.add('show');
}
function closeModal(id){
  document.getElementById(id).classList.remove('show');
}

let eventoContextoNota = null;

function openEventModal(tituloSugerido=''){
  document.getElementById('eventTitleInput').value = tituloSugerido;

  // tenta interpretar comando rápido embutido no texto sugerido
  const parsed = parseComandoRapido(tituloSugerido);
  const alvo = parsed || new Date();
  document.getElementById('eventDateInput').value = alvo.toISOString().slice(0,10);
  document.getElementById('eventTimeInput').value = alvo.toTimeString().slice(0,5);

  openModal('modalEvent');
}

/* ============================================================
   DRAWER DE CONFIGURAÇÃO (5 cliques no logo)
   ============================================================ */

let logoClickCount = 0;
let logoClickTimer = null;

function handleLogoClick(){
  logoClickCount++;
  clearTimeout(logoClickTimer);
  logoClickTimer = setTimeout(()=>{ logoClickCount = 0; }, 1200);

  if(logoClickCount >= 5){
    logoClickCount = 0;
    openConfigDrawer();
  }
}

function openConfigDrawer(){
  document.getElementById('cfgGoogleClientId').value = CONFIG.googleClientId || '';
  document.getElementById('cfgBinId').value = CONFIG.binId || '';
  document.getElementById('cfgBinKey').value = CONFIG.binKey || '';
  updateGoogleStatusUI();
  document.getElementById('syncStatus').textContent = (CONFIG.binId && CONFIG.binKey) ? 'configurado' : 'não configurado';
  if(CONFIG.binId && CONFIG.binKey) document.getElementById('syncStatus').classList.add('connected');

  document.getElementById('drawerOverlay').classList.add('show');
  document.getElementById('configDrawer').classList.add('show');
}

function closeConfigDrawer(){
  document.getElementById('drawerOverlay').classList.remove('show');
  document.getElementById('configDrawer').classList.remove('show');
}

function exportarDados(){
  const blob = new Blob([JSON.stringify(STATE, null, 2)], { type:'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `painel-jvm-backup-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function limparTodosDados(){
  if(!confirm('Isso vai apagar todas as notas, demandas e histórico local. Confirma?')) return;
  STATE = defaultState();
  saveState();
  renderAll();
  showToast('Dados limpos');
}

/* ============================================================
   RENDER GERAL + ESTADO DE SESSÃO (retomar de onde parei)
   ============================================================ */

function renderAll(){
  renderRecorrentes();
  renderNotes();
  renderDemands();
  renderEventos();
  renderSummary();
}

function salvarUltimoEstado(){
  const noteVal = document.getElementById('noteInput').value;
  STATE.lastOpenState = noteVal ? { noteDraft: noteVal } : null;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(STATE));
}

function restaurarUltimoEstado(){
  if(STATE.lastOpenState && STATE.lastOpenState.noteDraft){
    document.getElementById('noteInput').value = STATE.lastOpenState.noteDraft;
  }
}

/* ============================================================
   EVENT LISTENERS
   ============================================================ */

document.addEventListener('DOMContentLoaded', ()=>{
  capturarTokenDaURL();
  autoArquivarDemandas();
  renderAll();
  restaurarUltimoEstado();
  updateGoogleStatusUI();
  if(isGoogleConnected()) carregarEventosGoogle();
  if(CONFIG.binId && CONFIG.binKey) loadFromCloud();

  // logo — 5 cliques
  document.getElementById('logoMark').addEventListener('click', handleLogoClick);

  // notas
  document.getElementById('noteInput').addEventListener('keydown', (e)=>{
    if(e.ctrlKey && e.key==='Enter') salvarNota();
  });
  document.getElementById('noteInput').addEventListener('input', salvarUltimoEstado);
  document.getElementById('noteSearch').addEventListener('input', (e)=>renderNotes(e.target.value));
  document.getElementById('btnClearNotes').addEventListener('click', ()=>{
    if(!confirm('Apagar todo o histórico de anotações?')) return;
    STATE.notas = [];
    saveState();
    renderNotes();
  });

  // recorrentes
  document.getElementById('btnViewAllRecur').addEventListener('click', ()=>openModal('modalRecur'));
  document.getElementById('btnAddRecur').addEventListener('click', ()=>{
    const nome = prompt('Nome da nova recorrente:');
    if(!nome) return;
    const freq = prompt('Frequência (semanal / quinzenal / mensal):','semanal');
    if(!['semanal','quinzenal','mensal'].includes(freq)) return;
    adicionarRecorrente(nome, freq);
  });

  // demandas
  document.getElementById('btnNewDemand').addEventListener('click', ()=>openModal('modalDemand'));
  document.getElementById('btnSaveDemand').addEventListener('click', ()=>{
    const titulo = document.getElementById('demandTitleInput').value.trim();
    const prioridade = document.getElementById('demandPriorityInput').value;
    if(!titulo) return;
    STATE.demandas.push({ id:uid(), titulo, prioridade, concluida:false, criadoEm:new Date().toISOString() });
    saveState();
    renderDemands();
    renderSummary();
    document.getElementById('demandTitleInput').value = '';
    closeModal('modalDemand');
  });
  document.querySelectorAll('#demandFilters .filter-chip').forEach(chip=>{
    chip.addEventListener('click', ()=>{
      document.querySelectorAll('#demandFilters .filter-chip').forEach(c=>c.classList.remove('active'));
      chip.classList.add('active');
      demandFilterAtivo = chip.dataset.filter;
      renderDemands();
    });
  });

  // eventos
  document.getElementById('btnNewEvent').addEventListener('click', ()=>openEventModal());
  document.getElementById('btnConnectGoogle').addEventListener('click', ()=>{
    if(isGoogleConnected()){
      carregarEventosGoogle();
      showToast('Agenda atualizada');
    }else{
      openConfigDrawer();
    }
  });
  document.getElementById('btnSaveEvent').addEventListener('click', ()=>{
    const titulo = document.getElementById('eventTitleInput').value.trim();
    const data = document.getElementById('eventDateInput').value;
    const hora = document.getElementById('eventTimeInput').value || '09:00';
    if(!titulo || !data) return;
    criarEventoGoogle(titulo, `${data}T${hora}:00`);
    closeModal('modalEvent');
  });

  // config drawer
  document.getElementById('btnCloseDrawer').addEventListener('click', closeConfigDrawer);
  document.getElementById('drawerOverlay').addEventListener('click', closeConfigDrawer);
  document.getElementById('btnGoogleAuth').addEventListener('click', iniciarGoogleAuth);
  document.getElementById('btnSaveSync').addEventListener('click', async ()=>{
    const key = document.getElementById('cfgBinKey').value.trim();
    let binId = document.getElementById('cfgBinId').value.trim();
    const btn = document.getElementById('btnSaveSync');
    const statusEl = document.getElementById('syncStatus');
    if(!key){
      showToast('Cole a Master Key antes de salvar');
      return;
    }
    btn.disabled = true;
    statusEl.classList.remove('connected');
    statusEl.textContent = binId ? 'conectando...' : 'criando Bin automaticamente...';
    try{
      const criouAgora = !binId;
      if(!binId){
        binId = await criarBinAutomatico(key);
        document.getElementById('cfgBinId').value = binId;
      }
      CONFIG.binId = binId;
      CONFIG.binKey = key;
      saveConfig();
      statusEl.textContent = 'configurado';
      statusEl.classList.add('connected');
      showToast(criouAgora ? 'Bin criado automaticamente e sincronização ativada!' : 'Sincronização configurada');
      await loadFromCloud();
    }catch(e){
      statusEl.textContent = 'erro: '+(e.message||'falha desconhecida');
      showToast('Erro ao configurar: '+(e.message||'verifique a Master Key'));
    }finally{
      btn.disabled = false;
    }
  });
  document.getElementById('btnExportData').addEventListener('click', exportarDados);
  document.getElementById('btnWipeData').addEventListener('click', limparTodosDados);

  // fechar modais genéricos
  document.querySelectorAll('.modal-close').forEach(btn=>{
    btn.addEventListener('click', ()=>closeModal(btn.dataset.close));
  });
  document.querySelectorAll('.modal-overlay').forEach(overlay=>{
    overlay.addEventListener('click', (e)=>{
      if(e.target===overlay) overlay.classList.remove('show');
    });
  });
  document.addEventListener('keydown', (e)=>{
    if(e.key==='Escape'){
      document.querySelectorAll('.modal-overlay.show').forEach(o=>o.classList.remove('show'));
      closeConfigDrawer();
    }
  });

  // atualiza status de recorrentes a cada minuto (pulsos e resumo)
  setInterval(()=>{ renderRecorrentes(); renderSummary(); }, 60000);
});

window.addEventListener('beforeunload', salvarUltimoEstado);
