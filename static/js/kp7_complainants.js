/* KP Form 7 person selectors. Preserve the original asset path for upgrades. */
(function () {
  "use strict";
  const config = JSON.parse(document.getElementById("fb-config")?.textContent || "{}");
  if (Number(config.form_id) !== 7) return;
  const form = document.getElementById("formFields");
  const pickers = [];
  let saving = false;

  function refreshControls() {
    const blocked = saving || pickers.some(picker => !picker.ready);
    for (const id of ["saveBtn", "submitBtn"]) {
      const button = document.getElementById(id);
      if (button) button.disabled = blocked || (typeof _submitting !== "undefined" && _submitting);
    }
  }

  async function getJSON(url, options = {}) {
    const response = await fetch(url, {credentials: "same-origin", cache: "no-store", ...options});
    const data = await response.json().catch(() => ({error: "Reload Form 7 and try again."}));
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not load people.");
    return data;
  }

  function createPicker({panelId, prefix, role, nameCodes, addressCode}) {
    const panel = document.getElementById(panelId);
    if (!panel) throw new Error("Reload Form 7 to load both party selectors.");
    const plural = role + "s";
    const key = "kp7_" + plural;
    const el = suffix => document.getElementById(prefix + suffix);
    const search = el("PersonSearch"), results = el("SearchResults"), selected = el("SelectedPeople");
    const status = el("PartyStatus"), retry = el("ReloadPeople"), legacy = el("LegacyNames");
    const review = el("Review"), editor = el("Editor");
    let hidden = form.elements.namedItem(key);
    if (!hidden) {
      hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = key;
      panel.appendChild(hidden);
    }
    const field = code => form.querySelector('input[data-code="' + code + '"], textarea[data-code="' + code + '"]');
    let people = [], ready = false, keepLegacy = false, needsReview = false;
    let legacyNames = "", legacyAddress = "", timer, searchRequest;

    function tell(message, error = false) {
      status.textContent = message;
      status.classList.toggle("text-danger", error);
    }
    function countMessage(action) {
      return people.length + " " + (people.length === 1 ? role : plural) + " " + action + ".";
    }
    function rows() {
      return keepLegacy ? null : people.map(({person_id, address}) => ({person_id, address}));
    }
    function write(code, value) {
      const control = field(code);
      if (!control) return;
      control.readOnly = true;
      if (control.value === value) return;
      control.value = value;
      control.dispatchEvent(new Event("change", {bubbles: true}));
    }
    function sync() {
      hidden.value = JSON.stringify(rows());
      const names = keepLegacy ? legacyNames : people.map(p => p.name).join(", ");
      nameCodes.forEach(code => write(code, names));
      const addresses = people.map(p => p.address.trim());
      const same = addresses.length && addresses[0] && addresses.every(a => a === addresses[0]);
      const summary = same ? addresses[0] : people.map((p, i) => p.name + ": " + (addresses[i] || "Address not provided")).join("; ");
      write(addressCode, keepLegacy ? legacyAddress : summary);
    }
    function controls() {
      hidden.disabled = !ready; // FormData must keep the selection while saving.
      search.disabled = !ready || saving || keepLegacy;
      panel.querySelectorAll("textarea, button[data-remove]").forEach(control => { control.disabled = saving; });
      if (review) review.disabled = saving;
      refreshControls();
    }
    function render() {
      selected.replaceChildren();
      if (editor) editor.hidden = keepLegacy;
      if (review) {
        review.hidden = !needsReview;
        review.textContent = keepLegacy ? "Review respondent list" : "Keep saved respondent details";
      }
      for (const person of people) {
        const card = document.createElement("div");
        card.className = "border rounded-3 p-3 bg-white";
        const header = document.createElement("div");
        header.className = "d-flex justify-content-between align-items-center gap-2 mb-2";
        const name = document.createElement("strong");
        name.textContent = person.name;
        header.appendChild(name);
        if (person.linked) {
          const badge = document.createElement("span");
          badge.className = "badge text-bg-secondary";
          badge.textContent = "Recorded";
          header.appendChild(badge);
        } else {
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "btn btn-sm btn-outline-danger";
          remove.dataset.remove = String(person.person_id);
          remove.textContent = "Remove";
          remove.setAttribute("aria-label", "Remove " + person.name);
          remove.addEventListener("click", () => {
            people = people.filter(p => p.person_id !== person.person_id);
            render();
          });
          header.appendChild(remove);
        }
        const label = document.createElement("label");
        label.className = "form-label small mb-1";
        label.htmlFor = prefix + "Address_" + person.person_id;
        label.textContent = "Address for this complaint";
        const address = document.createElement("textarea");
        address.id = label.htmlFor;
        address.setAttribute("aria-label", "Address for " + person.name);
        address.className = "form-control";
        address.rows = 2;
        address.maxLength = 2000;
        address.value = person.address;
        address.addEventListener("input", () => { person.address = address.value; sync(); });
        card.append(header, label, address);
        selected.appendChild(card);
      }
      tell(keepLegacy ? "This form will keep the respondent details as originally entered."
        : people.length ? countMessage("selected") : "Search for a person to add the first " + role + ".");
      sync();
      controls();
    }
    async function load() {
      ready = false;
      retry.hidden = true;
      controls();
      tell("Loading " + plural + "…");
      try {
        const url = new URL(config.urls[key], window.location.origin);
        const sid = form.elements.submission_id?.value || config.submission_id;
        if (sid) url.searchParams.set("submission_id", sid);
        const data = await getJSON(url);
        people = data.people.map(p => ({...p, person_id: Number(p.person_id), address: p.address || ""}));
        legacyNames = data.legacy_names || "";
        legacyAddress = data.legacy_address || "";
        needsReview = keepLegacy = !!data.keep_legacy;
        const showLegacy = needsReview || (legacyNames && legacyNames !== people.map(p => p.name).join(", "));
        legacy.hidden = !showLegacy;
        if (showLegacy) {
          const notes = [];
          if (legacyNames) notes.push("Previously entered names: " + legacyNames);
          if (legacyAddress) notes.push("Previously entered address: " + legacyAddress);
          if (needsReview) notes.push("To replace these details, review each selected respondent and address before saving.");
          legacy.textContent = notes.join("\n");
          legacy.style.whiteSpace = "pre-line";
        }
        ready = true;
        render();
      } catch (error) {
        tell(error.message, true);
        retry.hidden = false;
      }
    }
    async function searchPeople(query) {
      if (!ready || saving || keepLegacy) return;
      searchRequest = new AbortController();
      const signal = searchRequest.signal;
      try {
        const url = new URL(config.urls.kp7_people_search, window.location.origin);
        url.searchParams.set("q", query);
        const data = await getJSON(url, {signal});
        if (signal.aborted || search.value.trim() !== query || saving || keepLegacy) return;
        results.replaceChildren();
        for (const person of data.results) {
          const id = Number(person.person_id);
          const button = document.createElement("button");
          button.type = "button";
          button.className = "list-group-item list-group-item-action";
          button.textContent = person.name + (person.address ? " – " + person.address : "");
          button.disabled = people.some(p => p.person_id === id);
          if (button.disabled) button.textContent += " (selected)";
          button.addEventListener("click", () => {
            if (saving || keepLegacy || people.some(p => p.person_id === id)) return;
            people.push({person_id: id, name: person.name, address: person.address || "", linked: false});
            search.value = "";
            results.replaceChildren();
            render();
            search.focus();
          });
          results.appendChild(button);
        }
        tell(data.results.length ? "Select a person from the results." : "No matching people found.");
      } catch (error) {
        if (error.name !== "AbortError") tell(error.message, true);
      }
    }

    function cancelSearch() {
      clearTimeout(timer);
      searchRequest?.abort();
      results.replaceChildren();
    }
    search.addEventListener("input", () => {
      cancelSearch();
      const query = search.value.trim();
      if (query.length >= 2) timer = setTimeout(() => searchPeople(query), 220);
    });
    search.addEventListener("keydown", e => {
      if (e.key === "Enter") e.preventDefault();
      if (e.key === "Escape") cancelSearch();
    });
    retry.addEventListener("click", load);
    review?.addEventListener("click", () => {
      keepLegacy = !keepLegacy;
      cancelSearch();
      render();
      if (!keepLegacy) search.focus();
    });

    return {
      key, load, get ready() { return ready; },
      serialize() {
        if (!ready) throw new Error("Wait for the " + plural + " to load, or try loading again.");
        if (!keepLegacy && !people.length && (role === "complainant" || needsReview || field(nameCodes[0])?.required)) {
          const message = "Select at least one " + role + ".";
          tell(message, true);
          search.focus();
          throw new Error(message);
        }
        return rows();
      },
      controls() {
        if (saving) cancelSearch();
        controls();
      },
      commit(savedPeople) {
        if (!Array.isArray(savedPeople)) return;
        people = savedPeople.map(p => ({...p, linked: true}));
        keepLegacy = needsReview = false;
        legacy.hidden = true;
        render();
        tell(countMessage("recorded on this case"));
      }
    };
  }

  if (!document.getElementById("kp7Complainants")) return;
  const complainants = createPicker({panelId: "kp7Complainants", prefix: "kp7", role: "complainant",
    nameCodes: ["complainants", "complainants_2", "complainant_printed_name"], addressCode: "contact_one"});
  const respondents = document.getElementById("kp7Respondents") && createPicker({panelId: "kp7Respondents", prefix: "kp7Respondent", role: "respondent",
    nameCodes: ["respondents", "respondents_2"], addressCode: "recipient_address"});
  pickers.push(complainants);
  if (respondents) pickers.push(respondents);
  form.addEventListener("submit", e => e.preventDefault());
  window.KP7Parties = {
    writeTo(formData) {
      for (const picker of pickers) formData.set(picker.key, JSON.stringify(picker.serialize()));
    },
    setSaving(value) { saving = value; pickers.forEach(picker => picker.controls()); },
    refreshControls,
    commitSaved(data) { complainants.commit(data.people); respondents?.commit(data.respondents); }
  };
  // A cached copy of the previous shared script may still use this name.
  window.KP7Complainants = {
    serialize: complainants.serialize, setSaving: window.KP7Parties.setSaving,
    commitSaved: window.KP7Parties.commitSaved
  };
  pickers.forEach(picker => picker.load());
})();
