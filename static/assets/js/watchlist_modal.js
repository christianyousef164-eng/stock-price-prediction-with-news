document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("stockListModal");
    const openBtn = document.getElementById("openStockList");
    const closeBtn = document.getElementById("closeModalBtn");
    const backArrow = document.getElementById("backArrow");

    const searchInput = document.getElementById("stockSearchInput");
    const searchResultsList = document.getElementById("searchResultsList");
    const loadingMessage = document.getElementById("stockListLoading");
    const currentWatchlist = document.getElementById("currentWatchlist");

    const saveChangesBtn = document.getElementById("saveChangesBtn");
    const cancelChangesBtn = document.getElementById("cancelChangesBtn");

    if (
        !modal ||
        !openBtn ||
        !closeBtn ||
        !backArrow ||
        !searchInput ||
        !searchResultsList ||
        !currentWatchlist ||
        !saveChangesBtn ||
        !cancelChangesBtn
    ) {
        console.error("Watchlist modal setup failed: missing DOM elements.");
        return;
    }

    const stockSearchUrl = modal.dataset.searchUrl;
    const watchlistApiUrl = modal.dataset.watchlistUrl;

    if (!stockSearchUrl || !watchlistApiUrl) {
        console.error("Watchlist modal setup failed: missing API URLs.");
        return;
    }

    const DEFAULT_STOCK_COUNT = 30;

    let userWatchlistMap = new Map();
    let initialWatchlistMap = new Map();
    let pendingChanges = new Map();

    function getCookie(name) {
        const cookieValue = document.cookie
            .split("; ")
            .find(row => row.startsWith(name + "="));
        return cookieValue ? decodeURIComponent(cookieValue.split("=")[1]) : "";
    }

    function getCurrentCheckedState(symbol) {
        if (pendingChanges.get(symbol) === "add") return true;
        if (pendingChanges.get(symbol) === "remove") return false;
        return initialWatchlistMap.has(symbol);
    }

    function renderWatchlistRow(symbol, companyName, checked = true) {
        return `
            <div class="stock-info">
                <span class="stock-symbol">${symbol}</span>
                <span class="stock-name">${companyName || symbol}</span>
            </div>
            <div class="form-check form-switch">
                <input
                    class="form-check-input add-stock-switch"
                    type="checkbox"
                    role="switch"
                    data-symbol="${symbol}"
                    ${checked ? "checked" : ""}
                >
            </div>
        `;
    }

    function renderMyWatchlist() {
        currentWatchlist.innerHTML = "";

        const visibleEntries = [];

        initialWatchlistMap.forEach((companyName, symbol) => {
            if (getCurrentCheckedState(symbol)) {
                visibleEntries.push([symbol, companyName]);
            }
        });

        pendingChanges.forEach((action, symbol) => {
            if (action === "add" && !initialWatchlistMap.has(symbol)) {
                visibleEntries.push([symbol, userWatchlistMap.get(symbol) || symbol]);
            }
        });

        if (visibleEntries.length === 0) {
            currentWatchlist.innerHTML =
                '<p class="text-muted text-center p-3">Your watchlist is currently empty.</p>';
            return;
        }

        visibleEntries
            .sort((a, b) => a[0].localeCompare(b[0]))
            .forEach(([symbol, companyName]) => {
                const item = document.createElement("div");
                item.className = "list-group-item search-result-item";
                item.innerHTML = renderWatchlistRow(symbol, companyName, true);
                currentWatchlist.appendChild(item);
            });
    }

    async function fetchWatchlist() {
        try {
            const response = await fetch(watchlistApiUrl, { credentials: "same-origin" });
            if (!response.ok) throw new Error("Failed to fetch watchlist.");

            const data = await response.json();
            userWatchlistMap = new Map();

            (data.watchlist || []).forEach(stock => {
                userWatchlistMap.set(stock.symbol, stock.company_name || stock.symbol);
            });

            initialWatchlistMap = new Map(userWatchlistMap);
            pendingChanges.clear();
            renderMyWatchlist();
        } catch (error) {
            console.error("Error fetching watchlist:", error);
            currentWatchlist.innerHTML =
                '<p class="text-danger text-center p-3">Could not load your watchlist.</p>';
        }
    }

    async function fetchStocks(query = "", isDefault = false) {
        if (!isDefault && query.length < 2) {
            searchResultsList.innerHTML =
                '<p class="text-muted text-center p-3">Start typing a symbol to search.</p>';
            return;
        }

        loadingMessage.style.display = "block";

        try {
            const url = new URL(stockSearchUrl, window.location.origin);
            if (query) url.searchParams.set("q", query);
            url.searchParams.set("limit", String(isDefault ? DEFAULT_STOCK_COUNT : 30));

            const response = await fetch(url.toString(), { credentials: "same-origin" });
            if (!response.ok) throw new Error("Search failed.");

            const data = await response.json();
            const stocks = data.stocks || [];

            searchResultsList.innerHTML = "";

            if (stocks.length === 0) {
                searchResultsList.innerHTML = isDefault
                    ? '<p class="text-muted text-center p-3">No default stocks available.</p>'
                    : '<p class="text-muted text-center p-3">No results found.</p>';
                return;
            }

            stocks.forEach(stock => {
                const checked = getCurrentCheckedState(stock.symbol);

                if (!userWatchlistMap.has(stock.symbol)) {
                    userWatchlistMap.set(stock.symbol, stock.company_name || stock.symbol);
                }

                const item = document.createElement("div");
                item.className = "list-group-item search-result-item";
                item.innerHTML = renderWatchlistRow(
                    stock.symbol,
                    stock.company_name,
                    checked
                );
                searchResultsList.appendChild(item);
            });
        } catch (error) {
            console.error("Search error:", error);
            searchResultsList.innerHTML =
                '<p class="text-danger text-center p-3">Search failed.</p>';
        } finally {
            loadingMessage.style.display = "none";
        }
    }

    function fetchDefaultStocks() {
        searchInput.value = "";
        fetchStocks("", true);
    }

    function handleToggleLocal(symbol, isChecked) {
        const wasInitiallySaved = initialWatchlistMap.has(symbol);

        if (isChecked && !wasInitiallySaved) {
            pendingChanges.set(symbol, "add");
        } else if (!isChecked && wasInitiallySaved) {
            pendingChanges.set(symbol, "remove");
        } else {
            pendingChanges.delete(symbol);
        }

        renderMyWatchlist();
        syncSearchToggle(symbol, isChecked);
    }

    function syncSearchToggle(symbol, isChecked) {
        searchResultsList
            .querySelectorAll(`.add-stock-switch[data-symbol="${symbol}"]`)
            .forEach(input => {
                input.checked = isChecked;
            });

        currentWatchlist
            .querySelectorAll(`.add-stock-switch[data-symbol="${symbol}"]`)
            .forEach(input => {
                input.checked = isChecked;
            });
    }

    async function executeToggleStock(symbol) {
        try {
            const response = await fetch(watchlistApiUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCookie("csrftoken"),
                },
                credentials: "same-origin",
                body: JSON.stringify({ symbol }),
            });

            if (!response.ok) return false;
            const data = await response.json();
            return data.status === "success";
        } catch (error) {
            console.error(`Error toggling ${symbol}:`, error);
            return false;
        }
    }

    async function handleSaveChanges() {
        if (pendingChanges.size === 0) {
            closeModal(false);
            return;
        }

        saveChangesBtn.disabled = true;
        cancelChangesBtn.disabled = true;
        saveChangesBtn.textContent = "Saving...";

        const entries = Array.from(pendingChanges.entries());

        for (const [symbol, action] of entries) {
            const success = await executeToggleStock(symbol);
            if (!success) continue;

            if (action === "add") {
                initialWatchlistMap.set(symbol, userWatchlistMap.get(symbol) || symbol);
            } else if (action === "remove") {
                initialWatchlistMap.delete(symbol);
            }
        }

        pendingChanges.clear();
        userWatchlistMap = new Map(initialWatchlistMap);
        renderMyWatchlist();

        saveChangesBtn.disabled = false;
        cancelChangesBtn.disabled = false;
        saveChangesBtn.textContent = "Save Changes";

        closeModal(true);
    }

    function handleCancelChanges() {
        pendingChanges.clear();
        userWatchlistMap = new Map(initialWatchlistMap);
        renderMyWatchlist();

        const currentQuery = searchInput.value.trim();
        if (currentQuery.length >= 2) {
            fetchStocks(currentQuery, false);
        } else {
            fetchDefaultStocks();
        }

        closeModal(false);
    }

    function openModal() {
        modal.style.display = "block";
        document.body.classList.add("modal-open-custom");
        fetchWatchlist();
        fetchDefaultStocks();
    }

    function closeModal(refreshPage) {
        modal.style.display = "none";
        document.body.classList.remove("modal-open-custom");

        if (refreshPage) {
            window.location.reload();
        }
    }

    openBtn.addEventListener("click", openModal);

    closeBtn.addEventListener("click", () => {
        if (pendingChanges.size > 0) {
            handleCancelChanges();
        } else {
            closeModal(false);
        }
    });

    backArrow.addEventListener("click", () => {
        if (pendingChanges.size > 0) {
            handleCancelChanges();
        } else {
            closeModal(false);
        }
    });

    window.addEventListener("click", event => {
        if (event.target === modal) {
            if (pendingChanges.size > 0) {
                handleCancelChanges();
            } else {
                closeModal(false);
            }
        }
    });

    searchInput.addEventListener("input", () => {
        const query = searchInput.value.trim();
        if (query.length >= 2) {
            fetchStocks(query, false);
        } else {
            fetchDefaultStocks();
        }
    });

    searchResultsList.addEventListener("change", event => {
        if (!event.target.classList.contains("add-stock-switch")) return;
        handleToggleLocal(event.target.dataset.symbol, event.target.checked);
    });

    currentWatchlist.addEventListener("change", event => {
        if (!event.target.classList.contains("add-stock-switch")) return;
        handleToggleLocal(event.target.dataset.symbol, event.target.checked);
    });

    saveChangesBtn.addEventListener("click", handleSaveChanges);
    cancelChangesBtn.addEventListener("click", handleCancelChanges);
});