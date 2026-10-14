const $ = (selector) => document.querySelector(selector);
const setupView = $("#setup-view");
const gameView = $("#game-view");
const playerCountInput = $("#player-count");
const playerList = $("#player-list");
const toast = $("#toast");
let gameState = null;
let selectedCard = null;
let busy = false;
let toastTimeout = null;
let onlineToken = null;
let onlineIdentity = null;
let onlinePollTimer = null;
let onlineMode = false;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function appendPenaltyMarks(parent, penalty, compact = false) {
  const display = element("span", `bull-penalty${compact ? " is-compact" : ""}`);
  display.setAttribute("role", "img");
  display.setAttribute("aria-label", `${penalty} bull penalty ${penalty === 1 ? "point" : "points"}`);
  const icons = element("span", "bull-icons", "🐂".repeat(penalty));
  icons.setAttribute("aria-hidden", "true");
  const count = element("span", "bull-count", `${penalty} pt${penalty === 1 ? "" : "s"}`);
  count.setAttribute("aria-hidden", "true");
  display.append(icons, count);
  parent.append(display);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove("is-visible"), 2800);
}

async function post(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作に失敗しました。");
  gameState = result;
  selectedCard = null;
  renderGame();
}

async function postOnline(path, payload = {}, preserveSelection = false) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, token: onlineToken }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "オンライン操作に失敗しました。");
  applyOnlineState(result, preserveSelection);
}

function applyOnlineState(result, preserveSelection = false) {
  if (
    gameState?.room_code === result.room_code &&
    Number.isInteger(gameState?.revision) &&
    Number.isInteger(result.revision) &&
    result.revision <= gameState.revision
  ) return;
  if (typeof result.token === "string") {
    onlineToken = result.token;
    onlineIdentity = { token: result.token, room_code: result.room_code };
    localStorage.setItem("nimmt-online-seat", JSON.stringify(onlineIdentity));
    delete result.token;
  }
  if (!(
    preserveSelection &&
    gameState?.room_code === result.room_code &&
    gameState?.turn === result.turn &&
    gameState?.phase === "choose" &&
    result.phase === "choose"
  )) selectedCard = null;
  gameState = result;
  onlineMode = true;
  $("#table-mode").textContent = "ONLINE ROOM";
  $("#new-game-button").hidden = true;
  $("#share-room-button").hidden = false;
  renderGame();
  if (gameState.phase === "lobby" || gameState.phase === "choose" || gameState.phase === "row") {
    startOnlinePolling();
  } else {
    stopOnlinePolling();
  }
}

async function onlineAction(path, payload = {}) {
  if (busy) return;
  busy = true;
  try {
    await postOnline(path, payload);
  } catch (error) {
    showToast(error.message);
    if (error.message.includes("参加トークンが無効") || error.message.includes("有効期限")) {
      abandonExpiredOnlineRoom();
    }
  } finally {
    busy = false;
  }
}

function startOnlinePolling() {
  if (onlinePollTimer !== null) return;
  onlinePollTimer = setInterval(async () => {
    if (busy || !onlineToken) return;
    try {
      const response = await fetch("/api/online/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: onlineToken }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not refresh the room.");
      applyOnlineState(result, true);
    } catch (error) {
      showToast(error.message);
      if (error.message.includes("参加トークンが無効") || error.message.includes("有効期限")) {
        abandonExpiredOnlineRoom();
      }
    }
  }, 1800);
}

function stopOnlinePolling() {
  if (onlinePollTimer !== null) clearInterval(onlinePollTimer);
  onlinePollTimer = null;
}

function abandonExpiredOnlineRoom() {
  localStorage.removeItem("nimmt-online-seat");
  onlineToken = null;
  onlineIdentity = null;
  stopOnlinePolling();
  gameView.hidden = true;
  setupView.hidden = false;
  $("#online-entry").hidden = false;
  $("#online-lobby").hidden = true;
  showSetupMode(true);
}

function showSetupMode(useOnline) {
  onlineMode = useOnline;
  $("#mode-tabs").hidden = Boolean(onlineIdentity);
  $("#local-setup").hidden = useOnline;
  $("#online-setup").hidden = !useOnline;
  $("#local-mode-tab").classList.toggle("is-active", !useOnline);
  $("#online-mode-tab").classList.toggle("is-active", useOnline);
  $("#local-mode-tab").setAttribute("aria-selected", String(!useOnline));
  $("#online-mode-tab").setAttribute("aria-selected", String(useOnline));
}

$("#local-mode-tab").addEventListener("click", () => showSetupMode(false));
$("#online-mode-tab").addEventListener("click", () => showSetupMode(true));

function updatePlayerSetup() {
  const count = Number(playerCountInput.value);
  $("#player-count-badge").textContent = `${count} PLAYERS`;
  const previous = Array.from(playerList.querySelectorAll(".player-row")).map((row) => ({
    name: row.querySelector("input").value,
    type: row.querySelector("select").value,
  }));
  playerList.replaceChildren();
  for (let index = 0; index < count; index += 1) {
    const row = element("div", "player-row");
    const name = document.createElement("input");
    name.type = "text";
    name.maxLength = 24;
    name.required = true;
    name.autocomplete = "off";
    name.setAttribute("aria-label", `Player ${index + 1} name`);
    name.value = previous[index]?.name || `Player ${index + 1}`;

    const type = document.createElement("select");
    type.setAttribute("aria-label", `Player ${index + 1} type`);
    for (const [value, label] of [
      ["human", "Human"],
      ["ai", "AI"],
      ["random", "Random"],
    ]) {
      const option = element("option", "", label);
      option.value = value;
      type.append(option);
    }
    type.value = previous[index]?.type || "human";
    row.append(name, type);
    playerList.append(row);
  }
}

playerCountInput.addEventListener("input", updatePlayerSetup);
updatePlayerSetup();

$("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const rows = Array.from(playerList.querySelectorAll(".player-row"));
  const players = rows.map((row) => row.querySelector("input").value.trim());
  const playerTypes = Object.fromEntries(
    rows.map((row, index) => [players[index], row.querySelector("select").value]),
  );
  busy = true;
  try {
    await post("/api/start", { players, player_types: playerTypes });
    setupView.hidden = true;
    gameView.hidden = false;
    $("#new-game-button").hidden = false;
  } catch (error) {
    showToast(error.message);
  } finally {
    busy = false;
  }
});

$("#create-room-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  try {
    const response = await fetch("/api/online/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("#online-host-name").value.trim(),
        max_players: Number($("#room-capacity").value),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not create the room.");
    applyOnlineState(result);
  } catch (error) {
    showToast(error.message);
  } finally {
    busy = false;
  }
});

$("#join-room-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  try {
    const response = await fetch("/api/online/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        room_code: $("#join-room-code").value.trim(),
        name: $("#join-player-name").value.trim(),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not join the room.");
    applyOnlineState(result);
  } catch (error) {
    showToast(error.message);
  } finally {
    busy = false;
  }
});

$("#share-room-button").addEventListener("click", async () => {
  const code = gameState?.room_code;
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
    showToast(`Room code ${code} copied.`);
  } catch {
    showToast(`Share this room code: ${code}`);
  }
});

$("#new-game-button").addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  try {
    await post("/api/reset");
    gameView.hidden = true;
    setupView.hidden = false;
    $("#new-game-button").hidden = true;
    $("#share-room-button").hidden = true;
    $("#table-mode").textContent = "LOCAL TABLE";
    gameState = null;
    selectedCard = null;
    onlineMode = false;
  } catch (error) {
    showToast(error.message);
  } finally {
    busy = false;
  }
});

function renderScores(state) {
  const scores = $("#score-list");
  scores.replaceChildren();
  const maxPenalty = Math.max(0, ...state.players.map((player) => player.penalty));
  const ranked = [...state.players].sort((left, right) => left.penalty - right.penalty);
  for (const [rank, player] of ranked.entries()) {
    const item = element(
      "article",
      `score-player${
        player.name === state.active_player ||
        (onlineMode && state.can_select_card && player.name === state.viewer)
          ? " is-active"
          : ""
      }`,
    );
    const name = element("div", "score-name", player.name);
    const type = element(
      "span",
      "score-type",
      player.type === "human" ? "HUMAN" : player.type.toUpperCase(),
    );
    name.append(type);
    const total = element("div", "score-total", `${player.penalty} pt`);
    const meta = element("div", "score-meta");
    meta.append(
      element("span", "", `#${rank + 1}  ROUND ${player.round_penalty} pt`),
      element("span", "", `${player.hand_count} cards left`),
    );
    const bar = element("div", "score-bar");
    const fill = element("span");
    fill.style.width = `${maxPenalty ? (player.penalty / maxPenalty) * 100 : 0}%`;
    bar.append(fill);
    item.append(name, total, meta, bar);
    scores.append(item);
  }
}

function renderLobby(state) {
  setupView.hidden = false;
  gameView.hidden = true;
  $("#mode-tabs").hidden = true;
  $("#online-entry").hidden = true;
  const lobby = $("#online-lobby");
  lobby.hidden = false;
  lobby.replaceChildren();

  const heading = element("div", "lobby-header");
  const title = element("div");
  title.append(
    element("p", "eyebrow", "ROOM CODE"),
    element("h2", "lobby-code", state.room_code),
  );
  const copy = element("button", "text-button", "Copy code");
  copy.type = "button";
  copy.addEventListener("click", () => $("#share-room-button").click());
  heading.append(title, copy);
  lobby.append(heading);
  lobby.append(
    element(
      "p",
      "online-intro",
      `Share the code with friends. Players ${state.players.length} / ${state.max_players}`,
    ),
  );

  const roster = element("div", "lobby-roster");
  for (const player of state.players) {
    const row = element("div", "lobby-player");
    const identity = element("div", "lobby-player-name");
    identity.append(
      element("strong", "", player.name),
      element(
        "span",
        "lobby-tag",
        player.is_host
          ? "HOST"
          : player.type === "human"
            ? "PLAYER"
            : player.type.toUpperCase(),
      ),
    );
    const status = element(
      "span",
      player.ready ? "lobby-ready" : "lobby-waiting",
      player.type !== "human" || player.ready ? "READY" : "NOT READY",
    );
    row.append(identity, status);
    roster.append(row);
  }
  lobby.append(roster);

  if (state.is_host) {
    const actions = element("div", "lobby-actions");
    const botType = document.createElement("select");
    botType.className = "secondary-button";
    botType.setAttribute("aria-label", "追加するボット");
    for (const [value, label] of [["ai", "Add AI"], ["random", "Add random"]]) {
      const option = element("option", "", label);
      option.value = value;
      botType.append(option);
    }
    botType.disabled = state.players.length >= state.max_players;
    botType.addEventListener("change", (event) => {
      event.currentTarget.dataset.playerType = event.currentTarget.value;
    });
    actions.append(botType);
    const addBot = element("button", "secondary-button", "Add bot");
    addBot.type = "button";
    addBot.disabled = state.players.length >= state.max_players;
    addBot.addEventListener("click", () =>
      onlineAction("/api/online/bot", {
        player_type: botType.dataset.playerType || botType.value,
      }),
    );
    const start = element("button", "primary-button", "Start game");
    start.type = "button";
    start.disabled = !state.can_start;
    start.addEventListener("click", () => onlineAction("/api/online/start"));
    actions.append(addBot, start);
    lobby.append(actions);
    lobby.append(
      element(
        "p",
        "lobby-note",
        state.can_start
          ? "Players are ready. Start the game when you're set."
          : "At least 2 players are required. All human players must be ready.",
      ),
    );
  } else {
    const actions = element("div", "lobby-actions");
    const ready = element(
      "button",
      "primary-button",
      state.players.find((player) => player.name === state.viewer)?.ready
        ? "Ready"
        : "I'm ready",
    );
    ready.type = "button";
    ready.disabled = state.players.find((player) => player.name === state.viewer)?.ready;
    ready.addEventListener("click", () => onlineAction("/api/online/ready", { ready: true }));
    actions.append(ready);
    lobby.append(actions);
    lobby.append(element("p", "lobby-note", "The host will start once everyone is ready."));
  }
}

function renderBoard(state) {
  const board = $("#board");
  board.replaceChildren();
  state.rows.forEach((row, index) => {
    const selectable =
      state.phase === "row" &&
      !state.awaiting_handoff &&
      state.row_candidates.includes(index);
    const card = element(
      selectable ? "button" : "div",
      `row-card${selectable ? " is-selectable" : ""}${row.cards.length >= 4 ? " is-danger" : ""}`,
    );
    if (selectable) {
      card.type = "button";
      card.addEventListener("click", () => chooseRow(index));
    }
    const top = element("div", "row-topline");
    top.append(
      element("span", "row-name", `ROW 0${index + 1}`),
      element("span", "row-max", row.max === null ? "—" : `MAX ${row.max}`),
    );
    const cards = element("div", "row-cards");
    if (row.cards.length) {
      for (const played of row.cards) {
        const miniCard = element("span", "mini-card");
        miniCard.append(
          element("span", "mini-card-value", String(played.value)),
        );
        appendPenaltyMarks(miniCard, played.penalty, true);
        cards.append(miniCard);
      }
    } else {
      cards.append(element("span", "row-empty", "Empty row"));
    }
    const bottom = element("div", "row-bottom");
    bottom.append(
      element("span", "", `${row.cards.length}/5 cards`),
      element("span", "row-take", `TAKE ${row.penalty} pt`),
    );
    const cardsLeft = 6 - row.cards.length;
    if (cardsLeft <= 1) {
      bottom.append(element("span", "row-danger", "NEXT CARD CLEARS ROW"));
    } else {
      bottom.append(element("span", "", `${cardsLeft} to six`));
    }
    const progress = element("span", "row-progress");
    progress.style.width = `${Math.min(100, (row.cards.length / 5) * 100)}%`;
    card.append(top, cards, bottom, progress);
    board.append(card);
  });
}

function chooseRow(rowIndex) {
  if (onlineMode) {
    onlineAction("/api/online/choose-row", { row_index: rowIndex });
  } else {
    runAction("/api/choose-row", { row_index: rowIndex });
  }
}

function addNotice(panel, { kind = "", kicker, title, description, action, actionLabel }) {
  const notice = element("div", `notice${kind ? ` ${kind}` : ""}`);
  const copy = element("div", "notice-copy");
  copy.append(element("p", "notice-kicker", kicker), element("p", "notice-title", title));
  if (description) copy.append(element("p", "notice-description", description));
  notice.append(copy);
  if (action) {
    const button = element("button", "primary-button");
    button.type = "button";
    button.append(element("span", "", actionLabel), element("span", "", "↗"));
    button.addEventListener("click", action);
    notice.append(button);
  }
  panel.append(notice);
  return notice;
}

async function runAction(path, payload = {}) {
  if (busy) return;
  if (onlineMode) {
    const onlineRoutes = {
      "/api/choose-card": "/api/online/choose-card",
      "/api/choose-row": "/api/online/choose-row",
      "/api/next-round": "/api/online/next-round",
    };
    const onlinePath = onlineRoutes[path];
    if (onlinePath) {
      await onlineAction(onlinePath, payload);
      return;
    }
  }
  busy = true;
  try {
    await post(path, payload);
  } catch (error) {
    showToast(error.message);
  } finally {
    busy = false;
  }
}

function renderResults(panel, state) {
  if (!state.played_cards?.length) return;
  const box = element("section", "play-history");
  box.append(
    element(
      "p",
      "notice-kicker",
      state.phase === "choose" ? "LAST TURN · LOWEST FIRST" : "CARDS PLAYED · LOWEST FIRST",
    ),
  );
  const results = new Map(
    (state.last_results || []).map((result) => [`${result.player}:${result.card_value}`, result]),
  );
  const list = element("div", "result-list");
  for (const play of state.played_cards) {
    const result = results.get(`${play.player}:${play.card_value}`);
    const line = element("div", "result-line");
    const card = element("span", "played-card");
    card.append(element("span", "played-card-value", String(play.card_value)));
    appendPenaltyMarks(card, play.penalty);
    line.append(
      element("strong", "", play.player),
      card,
    );
    if (result) {
      line.append(element("span", "", `→ Row ${result.row + 1}`));
      if (result.collection_reason === "sixth_card") {
        line.append(
          element(
            "span",
            "collection-message",
            `SIXTH CARD — collected ${result.collected_cards.length} cards from Row ${result.row + 1} for ${result.penalty} bull points. ${play.card_value} starts the new row.`,
          ),
        );
      } else if (result.collection_reason === "player_choice") {
        line.append(
          element(
            "span",
            "collection-message",
            `Row ${result.row + 1} collected · ${result.collected_cards.length} cards · ${result.penalty} bull points.`,
          ),
        );
      } else {
        line.append(
          element(
            "span",
            "result-penalty",
            result.penalty ? `+${result.penalty} bull points` : "No points",
          ),
        );
      }
    } else {
      line.append(element("span", "", "Resolving…"));
    }
    list.append(line);
  }
  box.append(list);
  panel.append(box);
}

function renderActivity(state) {
  const panel = $("#activity-panel");
  panel.replaceChildren();

  if (onlineMode && state.phase === "choose" && state.can_select_card) {
    const notice = addNotice(panel, {
      kicker: "CHOOSE ONE CARD",
      title: `${state.viewer}, choose a card`,
      description: `${state.selected_count} / ${state.selection_count} players selected. Cards stay hidden until everyone has chosen.`,
    });
    if (selectedCard !== null) {
      const confirm = element("button", "primary-button");
      confirm.type = "button";
      confirm.append(
        element("span", "", `Play ${selectedCard}`),
        element("span", "", "↗"),
      );
      confirm.addEventListener("click", () =>
        onlineAction("/api/online/choose-card", { card_value: selectedCard }),
      );
      notice.append(confirm);
    }
  } else if (onlineMode && state.phase === "choose") {
    const thisPlayer = state.players.find((player) => player.name === state.viewer);
    const message = thisPlayer?.has_selected
      ? "Your card is locked in. Waiting for the other players."
      : `${state.selected_count} / ${state.selection_count} players have chosen.`;
    addNotice(panel, {
      kicker: "WAITING FOR PLAYERS",
      title: "Waiting for all players",
      description: message,
    });
  } else if (state.phase === "choose" && state.awaiting_handoff) {
    const isFirstTurn = state.selected_count === 0;
    addNotice(panel, {
      kind: "is-action",
      kicker: isFirstTurn ? "PLAYER TURN" : "PASS THE DEVICE",
      title: isFirstTurn ? `${state.active_player}'s turn` : `Pass the device to ${state.active_player}`,
      description: "Make sure only the active player can see their hand before continuing.",
      action: () => runAction("/api/acknowledge-handoff"),
      actionLabel: "Start turn",
    });
  } else if (state.phase === "choose" && state.hand) {
    const notice = addNotice(panel, {
      kicker: "CHOOSE ONE CARD",
      title: `${state.active_player}'s turn — choose a card`,
      description: `${state.selected_count} / ${state.selection_count} players selected. Cards stay hidden until everyone has chosen.`,
    });
    if (selectedCard !== null) {
      const confirm = element("button", "primary-button");
      confirm.type = "button";
      confirm.append(
        element("span", "", `Play ${selectedCard}`),
        element("span", "", "↗"),
      );
      confirm.addEventListener("click", () =>
        runAction("/api/choose-card", { card_value: selectedCard }),
      );
      notice.append(confirm);
    }
  } else if (onlineMode && state.phase === "row" && !state.can_choose_row) {
    addNotice(panel, {
      kicker: "RESOLVING CARDS",
      title: `${state.active_player} is choosing a row`,
      description: "The table will update automatically once the row is selected.",
    });
  } else if (state.phase === "row" && state.awaiting_handoff) {
    addNotice(panel, {
      kind: "is-warning",
      kicker: "PASS THE DEVICE",
      title: `${state.active_player} chooses a row`,
      description: "This card cannot go on any row. Pass the device so the active player can choose a row.",
      action: () => runAction("/api/acknowledge-handoff"),
      actionLabel: "Choose row",
    });
  } else if (
    state.phase === "row" &&
    (state.resolution_card || (onlineMode && state.can_choose_row))
  ) {
    const notice = addNotice(panel, {
      kind: "is-warning",
      kicker: "TAKE A ROW",
      title: `${onlineMode ? state.viewer : state.active_player}, choose a row to take`,
      description: onlineMode
        ? "Your card is not higher than any row's top card. Check the bull points before choosing."
        : `Card ${state.resolution_card.value} is not higher than any row's top card. Check the bull points before choosing.`,
    });
    const choices = element("div", "row-choice-buttons");
    for (const rowIndex of state.row_candidates) {
      const row = state.rows[rowIndex];
      const button = element(
        "button",
        "",
        `Take Row ${rowIndex + 1} · ${row.penalty} pt`,
      );
      button.type = "button";
      button.addEventListener("click", () =>
        runAction("/api/choose-row", { row_index: rowIndex }),
      );
      choices.append(button);
    }
    notice.append(choices);
  } else if (state.phase === "round_over") {
    const winner = state.players.find((player) => player.name === state.winner);
    addNotice(panel, {
      kind: "is-action",
      kicker: "ROUND COMPLETE",
      title: `${state.winner} wins the round`,
      description: `${winner?.penalty ?? 0} total bull points. You can play another round.`,
      action: onlineMode
        ? state.is_host
          ? () => onlineAction("/api/online/next-round")
          : null
        : () => runAction("/api/next-round"),
      actionLabel: "Next round",
    });
    if (onlineMode && !state.is_host) {
      addNotice(panel, {
        kicker: "WAITING FOR HOST",
        title: "Waiting for the host to start the next round",
      });
    }
  }

  renderResults(panel, state);
}

function renderHand(state) {
  const section = $("#hand-section");
  const visible =
    state.phase === "choose" &&
    Array.isArray(state.hand) &&
    (!onlineMode || state.can_select_card);
  section.hidden = !visible;
  const hand = $("#hand");
  hand.replaceChildren();
  if (!visible) return;

  $("#hand-owner").textContent = `${onlineMode ? state.viewer : state.active_player}'s hand`;
  for (const card of state.hand) {
    const button = element(
      "button",
      `hand-card${selectedCard === card.value ? " is-selected" : ""}`,
    );
    button.type = "button";
    button.setAttribute(
      "aria-label",
      `Card ${card.value}, ${card.penalty} bull ${card.penalty === 1 ? "point" : "points"}`,
    );
    button.setAttribute("aria-pressed", String(selectedCard === card.value));
    button.append(
      element("span", "hand-value", String(card.value)),
    );
    appendPenaltyMarks(button, card.penalty);
    button.addEventListener("click", () => {
      selectedCard = selectedCard === card.value ? null : card.value;
      renderGame();
    });
    hand.append(button);
  }
}

function renderGame() {
  if (!gameState) return;
  if (onlineMode && gameState.phase === "lobby") {
    $("#share-room-button").hidden = false;
    $("#table-mode").textContent = "ONLINE ROOM";
    $("#online-entry").hidden = true;
    $("#online-lobby").hidden = false;
    renderLobby(gameState);
    return;
  }
  if (!gameState.players) return;
  setupView.hidden = true;
  gameView.hidden = false;
  $("#round-number").textContent = String(gameState.round);
  $("#turn-number").textContent = String(gameState.turn);
  $("#room-label").hidden = !onlineMode;
  $("#room-label").textContent = onlineMode ? `ROOM ${gameState.room_code}  ·  ` : "";
  $("#round-label").hidden = false;
  const titles = {
    choose: "Choose a card",
    resolve: "Resolving cards",
    row: "Choose a row",
    round_over: "Round over",
  };
  $("#phase-title").textContent = titles[gameState.phase] || "The table";
  const chosen = gameState.phase === "choose"
    ? `Cards chosen ${gameState.selected_count} / ${gameState.selection_count}`
    : gameState.phase === "round_over"
      ? "Round results"
      : "Lowest card resolves first";
  $("#turn-status").textContent = chosen;
  renderScores(gameState);
  renderBoard(gameState);
  renderActivity(gameState);
  renderHand(gameState);
}

async function restoreGame() {
  const storedSeat = localStorage.getItem("nimmt-online-seat");
  if (storedSeat) {
    try {
      onlineIdentity = JSON.parse(storedSeat);
      if (
        typeof onlineIdentity?.token !== "string" ||
        typeof onlineIdentity?.room_code !== "string"
      ) throw new Error("保存された参加情報が正しくありません。");
      onlineToken = onlineIdentity.token;
      const response = await fetch("/api/online/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: onlineToken }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "部屋に再接続できませんでした。");
      applyOnlineState(result, true);
      return;
    } catch (error) {
      localStorage.removeItem("nimmt-online-seat");
      onlineToken = null;
      onlineIdentity = null;
      showSetupMode(true);
      showToast(error.message);
      return;
    }
  }

  try {
    const response = await fetch("/api/state");
    const state = await response.json();
    if (!response.ok) throw new Error(state.error || "ゲーム状態を読み込めませんでした。");
    if (state.phase === "setup") return;
    gameState = state;
    onlineMode = false;
    $("#table-mode").textContent = "LOCAL TABLE";
    setupView.hidden = true;
    gameView.hidden = false;
    $("#new-game-button").hidden = false;
    $("#share-room-button").hidden = true;
    renderGame();
  } catch (error) {
    showToast(error.message);
  }
}

restoreGame();
