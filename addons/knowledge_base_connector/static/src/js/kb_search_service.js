/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

const kbSearchService = {
    dependencies: [],

    async start() {
        return {
            /**
             * Search the Knowledge Base via the backend proxy.
             * @param {string} query
             * @param {number} [limit=5]
             * @returns {Promise<Object>}
             */
            async search(query, limit = 5) {
                try {
                    return await rpc("/kb/search", { query, limit });
                } catch (e) {
                    console.warn("[KB] Search failed:", e);
                    return { results: [], total_count: 0, error: String(e) };
                }
            },
        };
    },
};

registry.category("services").add("kb_search", kbSearchService);
