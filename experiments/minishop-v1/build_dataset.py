"""Author independent minishop changes and freeze labels before model evaluation."""

import difflib
import hashlib
import json
import os
import subprocess
from pathlib import Path

from evals.schema import Case

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("MINISHOP_REPO", HERE.parent / "code-review-demo"))
BASE = "4353804fcf686c04a0ae4345bac223815d82f6b2"


def git(*args):
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={REPO.as_posix()}", "-C", str(REPO), *args],
        encoding="utf-8",
    )


def build():
    paths = git("ls-tree", "-r", "--name-only", BASE).splitlines()
    base = {p: git("show", f"{BASE}:{p}") for p in paths if p.endswith(".py") or p == "README.md"}
    cases, probes = [], {}

    def add(
        id,
        split,
        edits,
        *,
        anchor=None,
        defect=None,
        trigger=None,
        probe="",
        note="",
        category="logic",
    ):
        files = dict(base)
        for path, old, new in edits:
            if path not in files:
                assert old == ""
                files[path] = new
            else:
                assert files[path].count(old) == 1, (id, path, old)
                files[path] = files[path].replace(old, new)
        diff = ""
        for path in sorted({item[0] for item in edits}):
            diff += f"diff --git a/{path} b/{path}\n"
            if path not in base:
                diff += "new file mode 100644\n"
            diff += "".join(
                difflib.unified_diff(
                    base.get(path, "").splitlines(keepends=True),
                    files[path].splitlines(keepends=True),
                    fromfile=f"a/{path}" if path in base else "/dev/null",
                    tofile=f"b/{path}",
                )
            )
        gold = []
        if defect:
            path, evidence = anchor
            line = files[path].splitlines().index(evidence) + 1
            gold = [
                {
                    "id": id + "_g1",
                    "file_path": path,
                    "line": line,
                    "title": defect,
                    "defect": defect,
                    "trigger": trigger,
                    "severity": "medium",
                    "evidence": evidence,
                    "category": category,
                }
            ]
        case = Case.model_validate(
            {
                "id": id,
                "split": split,
                "source": f"https://github.com/hellolc666/code-review-demo/tree/{BASE}; authored mutation",
                "annotation_status": "curated_pending_human_signoff",
                "reviewed_by": "Codex, AI-simulated human annotator; no human signoff",
                "review_note": note or defect,
                "repo_files": files,
                "diff": diff,
                "gold_labels": gold,
            }
        )
        cases.append(case.model_dump(mode="json"))
        probes[id] = {"code": probe, "candidate_should_pass": not bool(gold)}

    # Development examples are not included in the reported A/B/C/D metrics.
    add(
        "dev_exact_stock",
        "dev",
        [("minishop/checkout.py", "available(sku) < quantity", "available(sku) <= quantity")],
        anchor=("minishop/checkout.py", "    if inventory.available(sku) <= quantity:"),
        defect="库存刚好等于购买数量时，结账被错误拒绝",
        trigger="Inventory({'book': 2}); checkout(..., 'book', 2)",
        probe="from minishop.checkout import checkout\nfrom minishop.inventory import Inventory\ni=Inventory({'book':2})\ncheckout(i,'book',2)\nassert i.available('book') == 0",
    )
    add(
        "dev_coupon_minimum",
        "dev",
        [
            (
                "minishop/pricing.py",
                "subtotal < coupon.minimum_cents",
                "subtotal <= coupon.minimum_cents",
            )
        ],
        anchor=("minishop/pricing.py", "        if subtotal <= coupon.minimum_cents:"),
        defect="金额恰好达到优惠券门槛时仍被拒绝",
        trigger="2000 分小计配 minimum_cents=2000 的优惠券",
        probe="from minishop.cart import Cart\nfrom minishop.catalog import Catalog,Product\nfrom minishop.pricing import quote,Coupon\nc=Cart(); c.add('book')\nassert quote(c,Catalog([Product('book','Book',2000)]),Coupon('X',10,2000)).discount_cents == 200",
    )
    add(
        "dev_readme",
        "dev",
        [("README.md", "运行全部测试：", "在仓库根目录运行全部测试：")],
        note="Clarifies test command working directory without changing behavior.",
        probe="pass",
    )
    add(
        "dev_inventory_compare",
        "dev",
        [
            (
                "minishop/inventory.py",
                "if self.available(sku) < quantity:",
                "if quantity > self.available(sku):",
            )
        ],
        note="Equivalent stock comparison for the same arguments; baseline tests remain valid.",
        probe="from minishop.inventory import Inventory\ni=Inventory({'x':2}); i.reserve('x',2)\nassert i.available('x') == 0",
    )

    add(
        "cart_overwrite",
        "test",
        [
            (
                "minishop/cart.py",
                "self._quantities[sku] = self._quantities.get(sku, 0) + quantity",
                "self._quantities[sku] = quantity",
            )
        ],
        anchor=("minishop/cart.py", "        self._quantities[sku] = quantity"),
        defect="重复添加同一商品覆盖旧数量，订单少买少计价",
        trigger="add('book',2) 后 add('book',1)，应为3却为1",
        probe="from minishop.cart import Cart\nc=Cart(); c.add('book',2); c.add('book')\nassert c.lines()[0].quantity == 3",
    )
    add(
        "catalog_inactive",
        "test",
        [
            (
                "minishop/catalog.py",
                "if product is None or not product.active:",
                "if product is None:",
            )
        ],
        anchor=("minishop/catalog.py", "        if product is None:"),
        defect="已下架商品通过 purchasable 校验，可继续被订单购买",
        trigger="active=False 的商品传给 Catalog.purchasable 或 OrderService.place",
        probe="from minishop.catalog import Catalog,Product\nc=Catalog([Product('x','X',100,active=False)])\ntry: c.purchasable('x')\nexcept ValueError: pass\nelse: raise AssertionError('inactive product accepted')",
    )
    add(
        "inventory_snapshot_alias",
        "test",
        [("minishop/inventory.py", "return dict(self._stock)", "return self._stock")],
        anchor=("minishop/inventory.py", "        return self._stock"),
        defect="库存快照暴露内部可变字典，调用者能绕过库存校验修改真实库存",
        trigger="snapshot()['book']=0 改变原库存；快照也随后续补货变化",
        probe="from minishop.inventory import Inventory\ni=Inventory({'book':2}); s=i.snapshot(); s['book']=0\nassert i.available('book') == 2",
    )
    old = '        if any(self.available(sku) < quantity for sku, quantity in quantities.items()):\n            raise ValueError("not enough stock")\n        for sku, quantity in quantities.items():\n            self._stock[sku] -= quantity'
    new = '        for sku, quantity in quantities.items():\n            if self.available(sku) < quantity:\n                raise ValueError("not enough stock")\n            self._stock[sku] -= quantity'
    add(
        "inventory_partial_reserve",
        "test",
        [("minishop/inventory.py", old, new)],
        anchor=("minishop/inventory.py", "            if self.available(sku) < quantity:"),
        defect="批量库存预留失去原子性，后续商品缺货时前面的库存已经扣除",
        trigger="stock book=2 pen=0；reserve_many({'book':1,'pen':1}) 抛错后 book 应仍为2",
        probe="from minishop.inventory import Inventory\ni=Inventory({'book':2,'pen':0})\ntry: i.reserve_many({'book':1,'pen':1})\nexcept ValueError: pass\nassert i.available('book') == 2",
    )
    add(
        "pricing_quantity",
        "test",
        [
            (
                "minishop/pricing.py",
                "catalog.purchasable(line.sku).price_cents * line.quantity",
                "catalog.purchasable(line.sku).price_cents",
            )
        ],
        anchor=(
            "minishop/pricing.py",
            "    subtotal = sum(catalog.purchasable(line.sku).price_cents for line in lines)",
        ),
        defect="小计忽略商品数量，多个同类商品只收取一个的价格",
        trigger="单价2000分、数量2，应得到4000分小计",
        probe="from minishop.cart import Cart\nfrom minishop.catalog import Catalog,Product\nfrom minishop.pricing import quote\nc=Cart(); c.add('book',2)\nassert quote(c,Catalog([Product('book','Book',2000)])).subtotal_cents == 4000",
    )
    add(
        "pricing_shipping_equal",
        "test",
        [
            (
                "minishop/pricing.py",
                "subtotal >= free_shipping_at_cents",
                "subtotal > free_shipping_at_cents",
            )
        ],
        anchor=(
            "minishop/pricing.py",
            "    shipping = 0 if subtotal > free_shipping_at_cents else shipping_cents",
        ),
        defect="小计恰好达到免邮门槛时仍收取运费",
        trigger="subtotal=5000、默认门槛5000，应免邮却收500分",
        probe="from minishop.cart import Cart\nfrom minishop.catalog import Catalog,Product\nfrom minishop.pricing import quote\nc=Cart(); c.add('book',2)\nassert quote(c,Catalog([Product('book','Book',2500)])).shipping_cents == 0",
    )
    add(
        "pricing_discount_fraction",
        "test",
        [
            (
                "minishop/pricing.py",
                "subtotal * coupon.percent_off // 100",
                "subtotal * coupon.percent_off / 100",
            )
        ],
        anchor=("minishop/pricing.py", "        discount = subtotal * coupon.percent_off / 100"),
        defect="折扣产生小数分，破坏整数金额及向下取整约定",
        trigger="999分小计打九折，discount 应为99分且总价1400分；修改后为99.9和1399.1",
        probe="from minishop.cart import Cart\nfrom minishop.catalog import Catalog,Product\nfrom minishop.pricing import quote,Coupon\nc=Cart(); c.add('x')\np=quote(c,Catalog([Product('x','X',999)]),Coupon('X',10))\nassert p.discount_cents == 99 and p.total_cents == 1400 and isinstance(p.total_cents,int)",
    )
    add(
        "orders_reserve_before_price",
        "test",
        [
            ("minishop/orders.py", "        price = quote(cart, self._catalog, coupon)\n", ""),
            (
                "minishop/orders.py",
                "        return Order(lines, price)",
                "        price = quote(cart, self._catalog, coupon)\n        return Order(lines, price)",
            ),
        ],
        anchor=("minishop/orders.py", "        price = quote(cart, self._catalog, coupon)"),
        defect="价格或优惠券校验移到扣库存之后，失败订单也消耗库存",
        trigger="购买1支笔却使用未达最低金额的优惠券；抛 ValueError 后库存应不变",
        probe="from minishop.cart import Cart\nfrom minishop.catalog import Catalog,Product\nfrom minishop.inventory import Inventory\nfrom minishop.orders import OrderService\nfrom minishop.pricing import Coupon\nc=Cart(); c.add('pen'); i=Inventory({'pen':2})\ntry: OrderService(Catalog([Product('pen','Pen',300)]),i).place(c,Coupon('X',10,2000))\nexcept ValueError: pass\nassert i.available('pen') == 2",
    )
    add(
        "orders_live_staging",
        "test",
        [
            (
                "minishop/orders.py",
                "staged = Inventory(self._inventory.snapshot())",
                "staged = self._inventory",
            )
        ],
        anchor=("minishop/orders.py", "        staged = self._inventory"),
        defect="预校验直接扣真实库存，成功订单重复扣减，后续失败也无法回滚",
        trigger="库存3购买1，checkout 和 reserve_many 各扣1，剩1而非2；后续缺货也会留下前项扣减",
        probe="from minishop.cart import Cart\nfrom minishop.catalog import Catalog,Product\nfrom minishop.inventory import Inventory\nfrom minishop.orders import OrderService\nc=Cart(); c.add('book'); i=Inventory({'book':3})\nOrderService(Catalog([Product('book','Book',2000)]),i).place(c)\nassert i.available('book') == 2",
    )
    old = '        self.assertEqual(checkout(inventory, "book", 2), {"sku": "book", "quantity": 2})'
    new = '        self.assertTrue(checkout(inventory, "book", 2))'
    add(
        "test_receipt_assertion",
        "test",
        [("tests/test_checkout.py", old, new)],
        anchor=("tests/test_checkout.py", new),
        category="regression",
        defect="返回值断言退化为真值判断，错误的非空收据也能通过测试",
        trigger="checkout 正确扣库存但返回错误 sku 或 quantity，修改后测试仍通过",
        probe="import importlib.util,unittest,io\ns=importlib.util.spec_from_file_location('receipt_test','tests/test_checkout.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\ndef wrong(i,sku,q):\n i.reserve(sku,q)\n return {'sku':'wrong','quantity':999}\nm.checkout=wrong\nr=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([m.CheckoutTests('test_exact_stock_can_be_purchased')]))\nassert not r.wasSuccessful(), 'broken receipt implementation escaped the test'",
    )

    add(
        "clean_cart_setdefault",
        "test",
        [
            (
                "minishop/cart.py",
                "self._quantities[sku] = self._quantities.get(sku, 0) + quantity",
                "self._quantities.setdefault(sku, 0)\n        self._quantities[sku] += quantity",
            )
        ],
        note="Equivalent aggregation after unchanged input validation; missing and repeated SKUs retain behavior.",
        probe="from minishop.cart import Cart\nc=Cart(); c.add('x',2); c.add('x'); assert c.lines()[0].quantity == 3",
    )
    add(
        "clean_catalog_guard",
        "test",
        [
            (
                "minishop/catalog.py",
                "if product is None or not product.active:",
                "if not product or not product.active:",
            )
        ],
        note="Product has no custom falsy behavior; None and inactive checks are equivalent.",
        probe="from minishop.catalog import Catalog,Product\nc=Catalog([Product('x','X',0)]); assert c.purchasable('x').price_cents == 0",
    )
    add(
        "clean_pricing_named_net",
        "test",
        [
            (
                "minishop/pricing.py",
                "    return Quote(subtotal, discount, shipping, subtotal - discount + shipping)",
                "    net_cents = subtotal - discount\n    return Quote(subtotal, discount, shipping, net_cents + shipping)",
            )
        ],
        note="Introduces a name for an unchanged integer expression; coupon and shipping behavior is preserved.",
        probe="pass",
    )
    add(
        "clean_orders_named_quantities",
        "test",
        [
            (
                "minishop/orders.py",
                "        self._inventory.reserve_many({line.sku: line.quantity for line in lines})",
                "        quantities = {line.sku: line.quantity for line in lines}\n        self._inventory.reserve_many(quantities)",
            )
        ],
        note="Extracts the same mapping after staging; validation and mutation order are unchanged.",
        probe="pass",
    )
    add(
        "clean_restock_named_total",
        "test",
        [
            (
                "minishop/inventory.py",
                "        self._stock[sku] = self.available(sku) + quantity",
                "        new_quantity = self.available(sku) + quantity\n        self._stock[sku] = new_quantity",
            )
        ],
        note="Names the new stock total without changing positive-quantity validation or missing-SKU behavior.",
        probe="from minishop.inventory import Inventory\ni=Inventory({}); i.restock('x',2); i.restock('x',1); assert i.available('x') == 3",
    )
    add(
        "clean_cart_total_feature",
        "test",
        [
            (
                "minishop/cart.py",
                "    def remove(self, sku: str) -> None:",
                '    def total_quantity(self) -> int:\n        """Return total item count, including repeated units."""\n        return sum(self._quantities.values())\n\n    def remove(self, sku: str) -> None:',
            ),
            (
                "tests/test_cart_total.py",
                "",
                "import unittest\n\nfrom minishop.cart import Cart\n\n\nclass CartTotalTests(unittest.TestCase):\n    def test_total_counts_units(self):\n        cart = Cart()\n        self.assertEqual(cart.total_quantity(), 0)\n        cart.add('book', 2)\n        cart.add('pen', 3)\n        self.assertEqual(cart.total_quantity(), 5)\n",
            ),
        ],
        note="Adds a total-unit-count method and an empty/multi-SKU test; no existing contract changes.",
        probe="pass",
    )

    data = HERE / "data"
    data.mkdir(exist_ok=True)
    target = data / "cases.jsonl"
    if target.exists():
        raise ValueError("Dataset already frozen; create a new version instead")
    raw = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases).encode("utf-8")
    target.write_bytes(raw)
    manifest = {
        "version": "minishop-v1-20",
        "base_commit": BASE,
        "repo_url": "https://github.com/hellolc666/code-review-demo",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cases": len(cases),
        "dev": 4,
        "test": 16,
        "annotation_status": "AI_simulated_pending_human_signoff",
        "human_signed_off": False,
        "scope": "10 defective + 6 clean test changes; 4 development cases; no security-specific gold",
    }
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (data / "probes.json").write_text(
        json.dumps(probes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for path, content in base.items():
        p = HERE / "base" / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for case in cases:
        p = data / "patches" / (case["id"] + ".diff")
        p.parent.mkdir(exist_ok=True)
        p.write_text(case["diff"], encoding="utf-8", newline="\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
