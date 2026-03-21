/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class KBSearchPanel extends Component {
    static template = "knowledge_base_connector.SearchPanel";
    static props = {
        onClose: Function,
    };

    setup() {
        this.kbSearch = useService("kb_search");
        this.state = useState({
            query: "",
            results: [],
            loading: false,
            searched: false,
        });
        this._debounceTimer = null;
    }

    onInput(ev) {
        this.state.query = ev.target.value;
        clearTimeout(this._debounceTimer);
        if (this.state.query.length >= 2) {
            this._debounceTimer = setTimeout(() => this.doSearch(), 300);
        }
    }

    onKeydown(ev) {
        if (ev.key === "Escape") {
            this.props.onClose();
        } else if (ev.key === "Enter") {
            this.doSearch();
        }
    }

    async doSearch() {
        const q = this.state.query.trim();
        if (!q) return;
        this.state.loading = true;
        const result = await this.kbSearch.search(q, 8);
        this.state.results = result.results || [];
        this.state.loading = false;
        this.state.searched = true;
    }

    formatScore(score) {
        return Math.round((score || 0) * 100) + "%";
    }
}


class KBSystrayItem extends Component {
    static template = "knowledge_base_connector.SystrayItem";
    static props = {};

    setup() {
        this.state = useState({ panelOpen: false });
    }

    togglePanel() {
        this.state.panelOpen = !this.state.panelOpen;
    }

    closePanel() {
        this.state.panelOpen = false;
    }
}

// Make KBSearchPanel available as a sub-component
KBSystrayItem.components = { KBSearchPanel };

registry.category("systray").add("knowledge_base_connector.search", {
    Component: KBSystrayItem,
}, { sequence: 50 });
