let deferredInstallPrompt = null;
const INSTALL_DISMISS_KEY = "malinka-install-dismissed-v1";
const mobileReceiptSelections = new WeakMap();

function formatWon(value) {
    const amount = Number.parseInt(value, 10);
    if (Number.isNaN(amount)) {
        return "0 ₩";
    }
    return `${amount.toLocaleString("ru-RU").replace(/\u00a0/g, " ")} ₩`;
}

function getFileFingerprint(file) {
    return [file.name, file.size, file.lastModified, file.type].join("::");
}

function mergeReceiptFiles(existingFiles, nextFiles) {
    const merged = [];
    const seen = new Set();

    [...existingFiles, ...nextFiles].forEach((file) => {
        const fingerprint = getFileFingerprint(file);
        if (seen.has(fingerprint)) {
            return;
        }
        seen.add(fingerprint);
        merged.push(file);
    });

    return merged;
}

function getReceiptFileInputs(form) {
    return Array.from(form.querySelectorAll('input[type="file"][name="receipt_photos"]'));
}

function getStoredReceiptFiles(form) {
    return mobileReceiptSelections.get(form) || [];
}

function getReceiptFiles(form) {
    const storedFiles = getStoredReceiptFiles(form);
    const inputFiles = getReceiptFileInputs(form).flatMap((input) => Array.from(input.files || []));
    return mergeReceiptFiles(inputFiles, storedFiles);
}

function syncReceiptAttachLabel(form) {
    const fileInput = form.querySelector('input[type="file"][name="receipt_photos"]');
    if (!fileInput?.id) {
        return;
    }

    const label = document.querySelector(`label[for="${fileInput.id}"]`);
    if (!label) {
        return;
    }

    if (!label.dataset.defaultText) {
        label.dataset.defaultText = label.textContent.trim();
    }

    const totalFiles = getReceiptFiles(form).length;
    label.textContent = totalFiles > 0 ? `Фото: ${totalFiles}` : label.dataset.defaultText;
}

function initReceiptAttachLabels() {
    document.querySelectorAll(".booking-complete-form").forEach((form) => {
        getReceiptFileInputs(form).forEach((input) => {
            input.addEventListener("change", () => {
                mobileReceiptSelections.delete(form);
                syncReceiptAttachLabel(form);
            });
        });
        syncReceiptAttachLabel(form);
    });
}

async function submitCompletionForm(form) {
    const storedFiles = getStoredReceiptFiles(form);
    if (storedFiles.length === 0) {
        form.submit();
        return;
    }

    const formData = new FormData(form);
    formData.delete("receipt_photos");
    storedFiles.forEach((file) => {
        formData.append("receipt_photos", file, file.name);
    });

    const response = await fetch(form.action, {
        method: (form.method || "POST").toUpperCase(),
        body: formData,
        credentials: "same-origin",
    });

    if (response.redirected) {
        mobileReceiptSelections.delete(form);
        window.location.assign(response.url);
        return;
    }

    if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
    }

    mobileReceiptSelections.delete(form);
    window.location.reload();
}

function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) {
        return;
    }

    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js").catch(() => {
            console.warn("Service worker registration failed");
        });
    });
}

function isStandaloneMode() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

function isIosInstallHintAvailable() {
    const userAgent = window.navigator.userAgent || "";
    const isIosDevice = /iPad|iPhone|iPod/.test(userAgent)
        || (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);
    const isExcludedBrowser = /CriOS|FxiOS|EdgiOS|OPiOS/.test(userAgent);
    return isIosDevice && !isExcludedBrowser;
}

function readInstallDismissState() {
    try {
        return window.localStorage.getItem(INSTALL_DISMISS_KEY) === "1";
    } catch {
        return false;
    }
}

function writeInstallDismissState() {
    try {
        window.localStorage.setItem(INSTALL_DISMISS_KEY, "1");
    } catch {
        // Ignore storage access issues in private browsing modes.
    }
}

function clearInstallDismissState() {
    try {
        window.localStorage.removeItem(INSTALL_DISMISS_KEY);
    } catch {
        // Ignore storage access issues in private browsing modes.
    }
}

function syncInstallUI() {
    const banner = document.getElementById("install-banner");
    const bannerText = document.getElementById("install-banner-text");
    const triggerButtons = Array.from(document.querySelectorAll("[data-install-trigger]"));

    document.body.classList.toggle("is-standalone-app", isStandaloneMode());

    if (!banner || !bannerText || triggerButtons.length === 0) {
        return;
    }

    const isDismissed = readInstallDismissState();
    const nativePromptAvailable = Boolean(deferredInstallPrompt);
    const showIosHint = !nativePromptAvailable && isIosInstallHintAvailable() && !isStandaloneMode();
    const shouldShowBanner = !isDismissed && !isStandaloneMode() && (nativePromptAvailable || showIosHint);

    banner.hidden = !shouldShowBanner;
    if (nativePromptAvailable) {
        bannerText.textContent = "Установите Malinka и открывайте CRM как отдельное приложение с иконкой на телефоне.";
    } else if (showIosHint) {
        bannerText.textContent = "На iPhone нажмите «Поделиться» в Safari и выберите «На экран Домой», чтобы открыть CRM как приложение.";
    }

    triggerButtons.forEach((button) => {
        button.hidden = !nativePromptAvailable;
    });
}

function initInstallPrompt() {
    const triggerButtons = Array.from(document.querySelectorAll("[data-install-trigger]"));
    const dismissButtons = Array.from(document.querySelectorAll("[data-install-dismiss]"));

    if (triggerButtons.length === 0) {
        return;
    }

    window.addEventListener("beforeinstallprompt", (event) => {
        event.preventDefault();
        deferredInstallPrompt = event;
        clearInstallDismissState();
        syncInstallUI();
    });

    window.addEventListener("appinstalled", () => {
        deferredInstallPrompt = null;
        writeInstallDismissState();
        syncInstallUI();
    });

    triggerButtons.forEach((button) => {
        button.addEventListener("click", async () => {
            if (!deferredInstallPrompt) {
                return;
            }
            deferredInstallPrompt.prompt();
            const choice = await deferredInstallPrompt.userChoice;
            deferredInstallPrompt = null;
            if (choice?.outcome !== "accepted") {
                writeInstallDismissState();
            }
            syncInstallUI();
        });
    });

    dismissButtons.forEach((button) => {
        button.addEventListener("click", () => {
            writeInstallDismissState();
            syncInstallUI();
        });
    });

    syncInstallUI();
}

function initDesktopMobileScale() {
    const stage = document.querySelector(".js-desktop-mobile-stage");
    const shell = document.querySelector(".js-desktop-mobile-shell");

    if (!stage || !shell) {
        return;
    }

    const desktopWidth = Number(stage.dataset.desktopMobileWidth || "1240");
    let resizeObserver = null;

    const sync = () => {
        const enabled = window.innerWidth <= 820;
        document.body.classList.toggle("force-desktop-mobile", enabled);

        if (!enabled) {
            stage.style.removeProperty("--desktop-mobile-stage-height");
            stage.style.removeProperty("--desktop-mobile-scale");
            stage.style.removeProperty("--desktop-mobile-width");
            return;
        }

        const scale = Math.min(window.innerWidth / desktopWidth, 1);
        stage.style.setProperty("--desktop-mobile-width", `${desktopWidth}px`);
        stage.style.setProperty("--desktop-mobile-scale", scale.toFixed(4));
        stage.style.setProperty(
            "--desktop-mobile-stage-height",
            `${Math.max(Math.ceil(shell.scrollHeight * scale), window.innerHeight)}px`
        );
    };

    const scheduleSync = () => {
        window.requestAnimationFrame(sync);
    };

    if ("ResizeObserver" in window) {
        resizeObserver = new ResizeObserver(scheduleSync);
        resizeObserver.observe(shell);
    }

    document.addEventListener("DOMContentLoaded", scheduleSync);
    window.addEventListener("load", scheduleSync);
    window.addEventListener("resize", scheduleSync);
    window.addEventListener("orientationchange", scheduleSync);
    window.addEventListener("pageshow", scheduleSync);

    scheduleSync();
    window.setTimeout(scheduleSync, 50);
    window.setTimeout(scheduleSync, 300);
}

function initClientModeToggle() {
    const modeSelect = document.querySelector(".js-client-mode");
    const clientBlock = document.querySelector(".js-client-select-block");
    const guestBlock = document.querySelector(".js-guest-block");

    if (!modeSelect || !clientBlock || !guestBlock) {
        return;
    }

    const sync = () => {
        const isExisting = modeSelect.value === "existing";
        clientBlock.style.display = isExisting ? "block" : "none";
        guestBlock.style.display = isExisting ? "none" : "block";
    };

    modeSelect.addEventListener("change", sync);
    sync();
}

function initPriceCalculator() {
    const form = document.querySelector(".js-appointment-form");
    if (!form) {
        return;
    }

    const masterSelect = form.querySelector(".js-master-select");
    const servicesSelect = form.querySelector(".js-services-select");
    const serviceCheckboxes = Array.from(form.querySelectorAll(".js-service-checkbox"));
    const panel = form.querySelector(".js-price-panel");
    const itemsContainer = form.querySelector(".js-price-items");
    const totalNode = form.querySelector(".js-price-total");
    const missingNode = form.querySelector(".js-price-missing");

    if (!masterSelect || !servicesSelect || !panel || !itemsContainer || !totalNode || !missingNode) {
        return;
    }

    const updatePrices = async () => {
        const masterId = masterSelect.value;
        const selectedServices = serviceCheckboxes.length > 0
            ? serviceCheckboxes.filter((input) => input.checked).map((input) => input.value)
            : Array.from(servicesSelect.selectedOptions).map((option) => option.value);

        if (!masterId || selectedServices.length === 0) {
            itemsContainer.innerHTML = "";
            totalNode.textContent = formatWon(0);
            missingNode.textContent = "";
            return;
        }

        const params = new URLSearchParams({ master_id: masterId });
        selectedServices.forEach((serviceId) => params.append("service_ids", serviceId));

        const response = await fetch(`${panel.dataset.priceUrl}?${params.toString()}`);
        const data = await response.json();

        itemsContainer.innerHTML = data.items
            .map((item) => `
                <div class="price-pill">
                    <span class="price-pill__name">${item.service_name}</span>
                    <strong class="price-pill__value">${formatWon(item.price)}</strong>
                </div>
            `)
            .join("");
        totalNode.textContent = formatWon(data.total);
        missingNode.textContent = data.missing_services.length
            ? `Не заданы цены: ${data.missing_services.join(", ")}`
            : "";
    };

    masterSelect.addEventListener("change", updatePrices);
    if (serviceCheckboxes.length > 0) {
        serviceCheckboxes.forEach((input) => {
            input.addEventListener("change", () => {
                input.closest(".service-choice")?.classList.toggle("is-selected", input.checked);
                updatePrices();
            });
        });
    } else {
        servicesSelect.addEventListener("change", updatePrices);
    }
    updatePrices();
}

function initPageModals() {
    const openers = document.querySelectorAll("[data-modal-open]");
    const closers = document.querySelectorAll("[data-modal-close]");

    if (openers.length === 0 && closers.length === 0) {
        return;
    }

    const toggleModal = (modalId, hidden) => {
        const modal = document.getElementById(modalId);
        if (!modal) {
            return;
        }
        modal.hidden = hidden;
    };

    openers.forEach((opener) => {
        opener.addEventListener("click", () => {
            toggleModal(opener.dataset.modalOpen, false);
        });
    });

    closers.forEach((closer) => {
        closer.addEventListener("click", () => {
            toggleModal(closer.dataset.modalClose, true);
        });
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") {
            return;
        }
        document.querySelectorAll(".appointment-modal").forEach((modal) => {
            modal.hidden = true;
        });
    });
}

function initServiceIconPicker() {
    const pickers = Array.from(document.querySelectorAll(".js-service-icon-picker"));

    if (pickers.length === 0) {
        return;
    }

    pickers.forEach((picker) => {
        const form = picker.closest("form");
        const hiddenInput = picker.querySelector(".js-service-icon-value");
        const previewImage = picker.querySelector(".js-service-icon-preview-image");
        const previewText = picker.querySelector(".js-service-icon-preview-text");
        const quickChoices = Array.from(picker.querySelectorAll("[data-service-icon-option]"));

        if (!form || !hiddenInput) {
            return;
        }

        const setSelectedIcon = (value) => {
            const fallback = quickChoices[0]?.dataset.serviceIconOption || "";
            const selected = (value || "").trim() || fallback;
            const choice = quickChoices.find((item) => item.dataset.serviceIconOption === selected);
            const selectedSrc = choice?.dataset.serviceIconSrc || "";
            hiddenInput.value = selected;

            if (previewImage) {
                previewImage.src = selectedSrc;
                previewImage.hidden = !selectedSrc;
            }
            if (previewText) {
                previewText.textContent = selectedSrc ? "" : selected;
                previewText.hidden = Boolean(selectedSrc);
            }

            quickChoices.forEach((choice) => {
                choice.classList.toggle("is-selected", choice.dataset.serviceIconOption === selected);
            });
        };

        form.setServiceIcon = setSelectedIcon;

        quickChoices.forEach((choice) => {
            choice.addEventListener("click", () => {
                setSelectedIcon(choice.dataset.serviceIconOption || "");
            });
        });

        setSelectedIcon(hiddenInput.value);
    });
}

function initServiceEditorModal() {
    const modal = document.getElementById("service-editor-modal");
    const triggers = Array.from(document.querySelectorAll(".js-service-editor-open"));
    const form = modal?.querySelector(".js-service-editor-form");

    if (!modal || !form || triggers.length === 0) {
        return;
    }

    const titleNode = modal.querySelector("#service-editor-title");
    const submitButton = modal.querySelector(".js-service-editor-submit");
    const serviceIdInput = form.querySelector('input[name="service_id"]');
    const serviceNameInput = form.querySelector('input[name="name"]');

    const populateForm = (trigger) => {
        const mode = trigger.dataset.serviceMode || "create";
        if (titleNode) {
            titleNode.textContent = trigger.dataset.serviceTitle || (mode === "edit" ? "Редактирование услуги" : "Новая услуга");
        }
        if (submitButton) {
            submitButton.textContent = trigger.dataset.serviceSubmit || (mode === "edit" ? "Сохранить" : "Добавить");
        }
        if (serviceIdInput) {
            serviceIdInput.value = mode === "edit" ? trigger.dataset.serviceId || "" : "";
        }
        if (serviceNameInput) {
            serviceNameInput.value = mode === "edit" ? trigger.dataset.serviceName || "" : "";
        }
        form.setServiceIcon?.(mode === "edit" ? trigger.dataset.serviceIcon || "" : "");
    };

    triggers.forEach((trigger) => {
        trigger.addEventListener("click", () => {
            populateForm(trigger);
        });
    });

    form.setServiceIcon?.(form.querySelector(".js-service-icon-value")?.value || "");
}

function initTeamMemberEditorModal() {
    const modal = document.getElementById("team-member-modal");
    const triggers = Array.from(document.querySelectorAll(".js-team-member-editor-open"));
    const form = modal?.querySelector(".js-team-member-form");

    if (!modal || !form) {
        return;
    }

    const titleNode = modal.querySelector("#team-member-title");
    const submitButton = modal.querySelector(".js-team-member-submit");
    const userIdInput = form.querySelector('input[name="user_id"]');
    const usernameInput = form.querySelector(".js-team-member-username");
    const fullNameInput = form.querySelector(".js-team-member-full-name");
    const passwordInput = form.querySelector(".js-team-member-password");
    const avatarInput = form.querySelector(".js-team-member-avatar");
    const avatarName = form.querySelector(".js-team-member-avatar-name");
    const roleSelect = form.querySelector(".js-team-member-role");
    const servicesSection = modal.querySelector(".js-team-master-services");
    const servicesDropdown = modal.querySelector(".js-team-services-dropdown");
    const servicesToggle = modal.querySelector(".js-team-services-toggle");
    const servicesPanel = modal.querySelector(".js-team-services-panel");
    const selectedEmpty = modal.querySelector(".js-team-selected-services-empty");
    const serviceCheckboxes = Array.from(modal.querySelectorAll(".js-team-service-checkbox"));
    const selectedCards = Array.from(modal.querySelectorAll(".js-team-selected-service"));
    const closeTargets = modal.querySelectorAll('[data-modal-close="team-member-modal"]');

    const parsePriceMap = (raw) => {
        const map = new Map();
        if (!raw) {
            return map;
        }
        raw.split(";").forEach((item) => {
            const [serviceId, price] = item.split(":");
            if (!serviceId) {
                return;
            }
            map.set(serviceId, price || "");
        });
        return map;
    };

    const syncAvatarName = () => {
        if (!avatarName || !avatarInput) {
            return;
        }
        avatarName.textContent = avatarInput.files?.[0]?.name || "Файл не выбран";
    };

    const cleanupModalUrl = () => {
        const url = new URL(window.location.href);
        let changed = false;
        ["member_edit", "member_create", "edit"].forEach((key) => {
            if (url.searchParams.has(key)) {
                url.searchParams.delete(key);
                changed = true;
            }
        });
        if (changed) {
            window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
        }
    };

    const setServicesPanelState = (expanded) => {
        if (servicesPanel) {
            servicesPanel.hidden = !expanded;
        }
        if (servicesToggle) {
            servicesToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        }
    };

    const syncSelectedServices = () => {
        let selectedCount = 0;
        serviceCheckboxes.forEach((checkbox) => {
            const card = modal.querySelector(`.js-team-selected-service[data-service-id="${checkbox.dataset.serviceId}"]`);
            const input = card?.querySelector(".js-team-service-price");
            const checked = checkbox.checked;
            if (card) {
                card.hidden = !checked;
            }
            if (input) {
                input.disabled = !checked;
            }
            if (checked) {
                selectedCount += 1;
            }
        });
        if (selectedEmpty) {
            selectedEmpty.hidden = selectedCount > 0;
        }
    };

    const syncRoleState = () => {
        const isMaster = roleSelect?.value === "master";
        if (servicesSection) {
            servicesSection.hidden = !isMaster;
        }
        serviceCheckboxes.forEach((checkbox) => {
            checkbox.disabled = !isMaster;
        });
        selectedCards.forEach((card) => {
            const input = card.querySelector(".js-team-service-price");
            const checkbox = modal.querySelector(`.js-team-service-checkbox[data-service-id="${card.dataset.serviceId}"]`);
            if (input) {
                input.disabled = !isMaster || !(checkbox?.checked);
            }
        });
        if (!isMaster) {
            setServicesPanelState(false);
        }
        if (selectedEmpty) {
            selectedEmpty.hidden = !isMaster || serviceCheckboxes.some((checkbox) => checkbox.checked);
        }
    };

    const populateForm = (trigger) => {
        const mode = trigger.dataset.memberMode || "create";
        const selectedIds = new Set((trigger.dataset.serviceIds || "").split(",").filter(Boolean));
        const priceMap = parsePriceMap(trigger.dataset.servicePrices || "");

        if (titleNode) {
            titleNode.textContent = trigger.dataset.memberTitle || (mode === "edit" ? "Редактировать сотрудника" : "Новый сотрудник");
        }
        if (submitButton) {
            submitButton.textContent = trigger.dataset.memberSubmit || (mode === "edit" ? "Сохранить изменения" : "Создать сотрудника");
        }
        if (userIdInput) {
            userIdInput.value = mode === "edit" ? (trigger.dataset.memberId || "") : "";
        }
        if (usernameInput) {
            usernameInput.value = mode === "edit" ? (trigger.dataset.memberUsername || "") : "";
        }
        if (fullNameInput) {
            fullNameInput.value = mode === "edit" ? (trigger.dataset.memberFullName || "") : "";
        }
        if (passwordInput) {
            passwordInput.value = "";
        }
        if (avatarInput) {
            avatarInput.value = "";
        }
        syncAvatarName();
        if (roleSelect) {
            roleSelect.value = trigger.dataset.memberRole || "master";
        }

        serviceCheckboxes.forEach((checkbox) => {
            const selected = selectedIds.has(checkbox.value);
            checkbox.checked = selected;
            const input = modal.querySelector(`.js-team-selected-service[data-service-id="${checkbox.dataset.serviceId}"] .js-team-service-price`);
            if (input) {
                input.value = selected ? (priceMap.get(checkbox.value) || "") : "";
            }
        });

        setServicesPanelState(false);
        syncSelectedServices();
        syncRoleState();
    };

    triggers.forEach((trigger) => {
        trigger.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            populateForm(trigger);
            modal.hidden = false;
        });
    });

    serviceCheckboxes.forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
            syncSelectedServices();
            syncRoleState();
        });
    });

    roleSelect?.addEventListener("change", syncRoleState);
    avatarInput?.addEventListener("change", syncAvatarName);
    servicesToggle?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!servicesPanel || roleSelect?.value !== "master") {
            return;
        }
        setServicesPanelState(servicesPanel.hidden);
    });
    closeTargets.forEach((target) => {
        target.addEventListener("click", () => {
            setServicesPanelState(false);
            cleanupModalUrl();
        });
    });

    document.addEventListener("click", (event) => {
        if (modal.hidden || !servicesDropdown || !servicesPanel || servicesPanel.hidden) {
            return;
        }
        if (servicesDropdown.contains(event.target)) {
            return;
        }
        setServicesPanelState(false);
    });

    syncSelectedServices();
    syncRoleState();
    syncAvatarName();
    setServicesPanelState(false);
}

function initClientEditorModal() {
    const modal = document.getElementById("client-editor-modal");
    const triggers = Array.from(document.querySelectorAll(".js-client-editor-open"));
    const form = modal?.querySelector(".js-client-form");

    if (!modal || !form) {
        return;
    }

    const titleNode = modal.querySelector("#client-editor-title");
    const submitButton = modal.querySelector(".js-client-submit");
    const clientIdInput = form.querySelector('input[name="client_id"]');
    const fullNameInput = form.querySelector(".js-client-full-name");
    const phoneInput = form.querySelector(".js-client-phone");
    const instagramInput = form.querySelector(".js-client-instagram");
    const notesInput = form.querySelector(".js-client-notes");
    const avatarInput = form.querySelector(".js-client-avatar");
    const avatarName = form.querySelector(".js-client-avatar-name");
    const closeTargets = modal.querySelectorAll('[data-modal-close="client-editor-modal"]');

    const syncAvatarName = () => {
        if (!avatarInput || !avatarName) {
            return;
        }
        avatarName.textContent = avatarInput.files?.[0]?.name || "Файл не выбран";
    };

    const cleanupModalUrl = () => {
        const url = new URL(window.location.href);
        let changed = false;
        ["client_edit", "client_create", "edit"].forEach((key) => {
            if (url.searchParams.has(key)) {
                url.searchParams.delete(key);
                changed = true;
            }
        });
        if (changed) {
            window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
        }
    };

    const populateForm = (trigger) => {
        const mode = trigger.dataset.clientMode || "create";

        if (titleNode) {
            titleNode.textContent = trigger.dataset.clientTitle || (mode === "edit" ? "Редактировать клиента" : "Новый клиент");
        }
        if (submitButton) {
            submitButton.textContent = trigger.dataset.clientSubmit || (mode === "edit" ? "Сохранить изменения" : "Сохранить клиента");
        }
        if (clientIdInput) {
            clientIdInput.value = mode === "edit" ? (trigger.dataset.clientId || "") : "";
        }
        if (fullNameInput) {
            fullNameInput.value = mode === "edit" ? (trigger.dataset.clientFullName || "") : "";
        }
        if (phoneInput) {
            phoneInput.value = mode === "edit" ? (trigger.dataset.clientPhone || "") : "";
        }
        if (instagramInput) {
            instagramInput.value = mode === "edit" ? (trigger.dataset.clientInstagram || "") : "";
        }
        if (notesInput) {
            notesInput.value = mode === "edit" ? (trigger.dataset.clientNotes || "") : "";
        }
        if (avatarInput) {
            avatarInput.value = "";
        }
        syncAvatarName();
    };

    triggers.forEach((trigger) => {
        trigger.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            populateForm(trigger);
            modal.hidden = false;
        });
    });

    avatarInput?.addEventListener("change", syncAvatarName);
    closeTargets.forEach((target) => {
        target.addEventListener("click", cleanupModalUrl);
    });

    syncAvatarName();
}

function initMasterRemindersSettings() {
    const form = document.querySelector(".js-master-reminders-form");
    if (!form) {
        return;
    }

    const list = form.querySelector(".js-master-reminders-list");
    const minutesInput = form.querySelector(".js-master-reminder-minutes");
    const addButton = form.querySelector("[data-master-reminder-add]");
    const emptyNode = form.querySelector(".js-master-reminders-empty");
    const errorNode = form.querySelector(".js-master-reminders-error");

    if (!list || !minutesInput || !addButton || !emptyNode || !errorNode) {
        return;
    }

    const syncEmpty = () => {
        emptyNode.hidden = list.querySelectorAll(".master-reminder-item").length > 0;
    };

    const showError = (message) => {
        errorNode.textContent = message;
        errorNode.hidden = !message;
    };

    const hasItem = (minutes) => Boolean(list.querySelector(`.master-reminder-item[data-minutes="${minutes}"]`));

    const createItem = (minutes) => {
        const item = document.createElement("div");
        item.className = "master-reminder-item";
        item.dataset.minutes = String(minutes);

        const hiddenInput = document.createElement("input");
        hiddenInput.type = "hidden";
        hiddenInput.name = "reminder_offsets";
        hiddenInput.value = String(minutes);

        const label = document.createElement("span");
        label.textContent = `За ${minutes} мин.`;

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "master-reminder-item__remove";
        removeButton.setAttribute("aria-label", "Удалить интервал");
        removeButton.textContent = "×";

        item.append(hiddenInput, label, removeButton);
        return item;
    };

    addButton.addEventListener("click", () => {
        showError("");
        const value = Number.parseInt(minutesInput.value || "", 10);
        if (!Number.isInteger(value) || value < 1 || value > 1440) {
            showError("Введите число от 1 до 1440.");
            return;
        }
        if (hasItem(value)) {
            showError("Такой интервал уже добавлен.");
            return;
        }
        list.appendChild(createItem(value));
        minutesInput.value = "";
        syncEmpty();
    });

    list.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
            return;
        }
        if (!target.classList.contains("master-reminder-item__remove")) {
            return;
        }
        target.closest(".master-reminder-item")?.remove();
        showError("");
        syncEmpty();
    });

    syncEmpty();
}

function initConfirmModal() {
    const modal = document.getElementById("confirm-modal");
    const forms = document.querySelectorAll(".js-confirm-form");

    if (!modal || forms.length === 0) {
        return;
    }

    const messageNode = modal.querySelector("#confirm-modal-message");
    const acceptButton = modal.querySelector("[data-confirm-accept]");
    const cancelButton = modal.querySelector("[data-confirm-cancel]");
    const closeTargets = modal.querySelectorAll("[data-confirm-close]");
    const detailsNode = modal.querySelector("#confirm-modal-details");
    const commentInput = modal.querySelector("#confirm-modal-comment");
    const errorNode = modal.querySelector("#confirm-modal-error");
    let pendingForm = null;

    const closeModal = () => {
        modal.hidden = true;
        pendingForm = null;
        if (detailsNode) {
            detailsNode.hidden = true;
        }
        if (commentInput) {
            commentInput.value = "";
        }
        if (errorNode) {
            errorNode.hidden = true;
            errorNode.textContent = "";
        }
    };

    forms.forEach((form) => {
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            pendingForm = form;
            messageNode.textContent = form.dataset.confirmMessage || "Подтвердите действие.";
            const isCompletionForm = form.classList.contains("booking-complete-form");
            if (detailsNode) {
                detailsNode.hidden = !isCompletionForm;
            }
            if (commentInput) {
                const hiddenCommentInput = form.querySelector('input[name="completion_comment"]');
                commentInput.value = hiddenCommentInput?.value || "";
            }
            if (errorNode) {
                errorNode.hidden = true;
                errorNode.textContent = "";
            }
            modal.hidden = false;
        });
    });

    acceptButton?.addEventListener("click", async () => {
        if (!pendingForm) {
            closeModal();
            return;
        }
        const hiddenCommentInput = pendingForm.querySelector('input[name="completion_comment"]');
        if (hiddenCommentInput && commentInput) {
            hiddenCommentInput.value = commentInput.value.trim();
        }
        if (pendingForm.classList.contains("booking-complete-form")) {
            const hasComment = Boolean(commentInput?.value.trim());
            const hasPhoto = getReceiptFiles(pendingForm).length > 0;
            if (!hasComment && !hasPhoto) {
                if (errorNode) {
                    errorNode.textContent = "Добавьте фото или комментарий, чтобы завершить запись.";
                    errorNode.hidden = false;
                }
                return;
            }
        }
        const targetForm = pendingForm;
        closeModal();
        try {
            await submitCompletionForm(targetForm);
        } catch {
            if (errorNode) {
                errorNode.textContent = "Не удалось отправить фото. Повторите еще раз.";
                errorNode.hidden = false;
            }
            pendingForm = targetForm;
            modal.hidden = false;
            if (detailsNode) {
                detailsNode.hidden = false;
            }
        }
    });

    cancelButton?.addEventListener("click", closeModal);
    closeTargets.forEach((target) => target.addEventListener("click", closeModal));

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.hidden) {
            closeModal();
        }
    });
}

function initImageLightbox() {
    const modal = document.getElementById("image-lightbox");
    const image = document.getElementById("image-lightbox-image");

    if (!modal || !image) {
        return;
    }

    const closeModal = () => {
        modal.hidden = true;
        image.removeAttribute("src");
        image.alt = "";
    };

    document.querySelectorAll("[data-lightbox-src]").forEach((trigger) => {
        trigger.addEventListener("click", (event) => {
            event.preventDefault();
            image.src = trigger.dataset.lightboxSrc || "";
            image.alt = trigger.dataset.lightboxAlt || "";
            modal.hidden = false;
        });
    });

    modal.querySelectorAll("[data-lightbox-close]").forEach((closer) => {
        closer.addEventListener("click", closeModal);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.hidden) {
            closeModal();
        }
    });
}

registerServiceWorker();
initInstallPrompt();
initDesktopMobileScale();
initClientModeToggle();
initPriceCalculator();
initPageModals();
initServiceIconPicker();
initServiceEditorModal();
initTeamMemberEditorModal();
initClientEditorModal();
initMasterRemindersSettings();
initReceiptAttachLabels();
initConfirmModal();
initImageLightbox();
initFilterMenus();
initMobileNav();

function initFilterMenus() {
    const allFilters = document.querySelectorAll("details.filter-menu");
    if (!allFilters.length) return;

    // Close all filter menus on page load (fixes stale open state after navigation)
    allFilters.forEach((d) => { d.removeAttribute("open"); });

    // Accordion behavior: close others when one opens
    allFilters.forEach((details) => {
        details.addEventListener("toggle", () => {
            if (details.open) {
                allFilters.forEach((other) => {
                    if (other !== details) other.removeAttribute("open");
                });
            }
        });
    });

    // Close all filter menus on outside click
    document.addEventListener("click", (e) => {
        if (!e.target.closest("details.filter-menu")) {
            allFilters.forEach((d) => { d.removeAttribute("open"); });
        }
    });
}

function initMobileNav() {
    const sheet = document.getElementById("mobile-nav-sheet");
    const toggle = document.querySelector("[data-mobile-nav-toggle]");

    if (!sheet || !toggle) {
        return;
    }

    const closeTargets = sheet.querySelectorAll("[data-mobile-nav-close]");
    const links = sheet.querySelectorAll(".mobile-nav-sheet__link");

    const openSheet = () => {
        sheet.hidden = false;
        sheet.offsetHeight;
        sheet.classList.add("is-open");
        toggle.setAttribute("aria-expanded", "true");
        document.body.classList.add("mobile-nav-open");
    };

    const closeSheet = () => {
        sheet.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
        document.body.classList.remove("mobile-nav-open");
        window.setTimeout(() => {
            if (!sheet.classList.contains("is-open")) {
                sheet.hidden = true;
            }
        }, 240);
    };

    toggle.addEventListener("click", () => {
        if (sheet.classList.contains("is-open")) {
            closeSheet();
        } else {
            openSheet();
        }
    });

    closeTargets.forEach((target) => target.addEventListener("click", closeSheet));
    links.forEach((link) => link.addEventListener("click", closeSheet));

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && sheet.classList.contains("is-open")) {
            closeSheet();
        }
    });
}

initPhotoSheet();

function initPhotoSheet() {
    const sheet = document.getElementById("photo-sheet");
    if (!sheet) return;

    const backdrop = document.getElementById("photo-sheet-backdrop");
    const cancelBtn = document.getElementById("photo-sheet-cancel");
    const cameraBtn = document.getElementById("photo-sheet-camera");
    const galleryBtn = document.getElementById("photo-sheet-gallery");
    const cameraInput = document.getElementById("photo-sheet-input-camera");
    const galleryInput = document.getElementById("photo-sheet-input-gallery");

    const isMobile = () => window.matchMedia("(max-width: 820px)").matches || navigator.maxTouchPoints > 0;

    if (!backdrop || !cancelBtn || !cameraBtn || !galleryBtn || !cameraInput || !galleryInput) {
        return;
    }

    let activeForm = null;

    function openSheet(originalInput) {
        activeForm = originalInput.form;
        if (!activeForm) {
            return;
        }
        sheet.hidden = false;
        sheet.offsetHeight;
        sheet.classList.add("is-open");
        sheet.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
    }

    function closeSheet(resetTarget = true) {
        sheet.classList.remove("is-open");
        sheet.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
        if (resetTarget) {
            activeForm = null;
        }
        setTimeout(() => { sheet.hidden = true; }, 350);
    }

    function openNativePicker(sourceInput) {
        if (!activeForm) {
            closeSheet();
            return;
        }

        sourceInput.value = "";

        try {
            if (typeof sourceInput.showPicker === "function") {
                sourceInput.showPicker();
            } else {
                sourceInput.click();
            }
        } catch {
            sourceInput.click();
        }

        closeSheet(false);
    }

    function storeSelectedFiles(sourceInput) {
        if (!activeForm) {
            sourceInput.value = "";
            return;
        }

        const files = Array.from(sourceInput.files || []);
        if (files.length > 0) {
            const combinedFiles = mergeReceiptFiles(getStoredReceiptFiles(activeForm), files);
            mobileReceiptSelections.set(activeForm, combinedFiles);
            syncReceiptAttachLabel(activeForm);
        }
        sourceInput.value = "";
        activeForm = null;
    }

    document.addEventListener("click", (e) => {
        if (!isMobile()) return;

        const label = e.target.closest(".booking-attach-label");
        if (!label) return;

        const forAttr = label.getAttribute("for");
        if (!forAttr) return;

        const originalInput = document.getElementById(forAttr);
        if (!originalInput || originalInput.type !== "file") return;

        e.preventDefault();
        openSheet(originalInput);
    }, true);

    cameraBtn.addEventListener("click", () => {
        openNativePicker(cameraInput);
    });

    galleryBtn.addEventListener("click", () => {
        openNativePicker(galleryInput);
    });

    cameraInput.addEventListener("change", () => {
        storeSelectedFiles(cameraInput);
    });

    galleryInput.addEventListener("change", () => {
        storeSelectedFiles(galleryInput);
    });

    cancelBtn.addEventListener("click", () => {
        closeSheet();
    });
    backdrop.addEventListener("click", () => {
        closeSheet();
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && sheet.classList.contains("is-open")) {
            closeSheet();
        }
    });
}
