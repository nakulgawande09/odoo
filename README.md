# Odoo

[![Build Status](https://runbot.odoo.com/runbot/badge/flat/1/master.svg)](https://runbot.odoo.com/runbot)
[![Tech Doc](https://img.shields.io/badge/master-docs-875A7B.svg?style=flat&colorA=8F8F8F)](https://www.odoo.com/documentation/master)
[![Help](https://img.shields.io/badge/master-help-875A7B.svg?style=flat&colorA=8F8F8F)](https://www.odoo.com/forum/help-1)
[![Nightly Builds](https://img.shields.io/badge/master-nightly-875A7B.svg?style=flat&colorA=8F8F8F)](https://nightly.odoo.com/)

Odoo is a suite of web based open source business apps.

The main Odoo Apps include an [Open Source CRM](https://www.odoo.com/page/crm),
[Website Builder](https://www.odoo.com/app/website),
[eCommerce](https://www.odoo.com/app/ecommerce),
[Warehouse Management](https://www.odoo.com/app/inventory),
[Project Management](https://www.odoo.com/app/project),
[Billing &amp; Accounting](https://www.odoo.com/app/accounting),
[Point of Sale](https://www.odoo.com/app/point-of-sale-shop),
[Human Resources](https://www.odoo.com/app/employees),
[Marketing](https://www.odoo.com/app/social-marketing),
[Manufacturing](https://www.odoo.com/app/manufacturing),
[...](https://www.odoo.com/)

Odoo Apps can be used as stand-alone applications, but they also integrate seamlessly so you get
a full-featured [Open Source ERP](https://www.odoo.com) when you install several Apps.

## Getting started with Odoo

For a standard installation please follow the [Setup instructions](https://www.odoo.com/documentation/master/administration/install/install.html)
from the documentation.

To learn the software, we recommend the [Odoo eLearning](https://www.odoo.com/slides),
or [Scale-up, the business game](https://www.odoo.com/page/scale-up-business-game).
Developers can start with [the developer tutorials](https://www.odoo.com/documentation/master/developer/howtos.html).

## Security

If you believe you have found a security issue, check our [Responsible Disclosure page](https://www.odoo.com/security-report)
for details and get in touch with us via email.

make dev # Install all deps
make test # Run 80 tests
make demo # Start services + seed 5 demo documents
make demo-search q='return policy' # Search from CLI
make demo-voip q='shipping info' # Test VOIP webhook
make clean # Tear down everything


Run main Odoo:
ccd ~/Desktop/me/odoo/odoo
python odoo-bin --addons-path=addons -d odoo -r odoo -w odoo
Key flags:

--addons-path=addons — path to addon modules
-d odoo — database name (auto-created on first run)
-r odoo / -w odoo — DB username / password
Then open http://localhost:8069 in your browser.



```
python odoo-bin --addons-path=addons,odoo/addons,odoo-kb/odoo_addon -d odoo -r odoo -w odoo --db_host=localhost -i base
```

Run Addon:
```
python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8100
```

Update kb_connector:
The button is in the XML. The module just needs to be upgraded. Run:

```
./odoo-bin -d odoo -u kb_connector --stop-after-init \
  --db_host=localhost --db_port=5432 --db_user=odoo --db_password=odoo \
  --addons-path=odoo/addons,addons,odoo-kb/odoo_addon

```

Then restart Odoo normally. The "Test TTS" button (with the speaker icon) will appear in the header between "Test Query" and "Sync to KB Service".
