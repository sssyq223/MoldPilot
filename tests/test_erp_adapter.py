import json
from types import SimpleNamespace

import httpx
import pytest

from domain_packs.mold import erp_adapter
from domain_packs.mold.ports.errors import DomainError


def test_outsource_execution_context_reads_registered_erp_facts_without_sensitive_fields(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if path.endswith('/entrust/project/list'):
            return httpx.Response(200, json={'code': 200, 'data': {'rows': [
                {'id': 7, 'project_no': 'P-001', 'name': '整套委外', 'status': 'active', 'secret': 'hidden'},
            ]}})
        if path.endswith('/entrust/production/list'):
            return httpx.Response(200, json={'code': 200, 'data': [{
                'order_id': 11, 'order_no': 'EO-11', 'project_no': 'P-001', 'stage': 'producing',
                'overall_pct': 40, 'parts': [], 'internal_cost': 'hidden',
            }]})
        if path.endswith('/entrust/fulfillment/orders'):
            return httpx.Response(200, json={'code': 200, 'data': [
                {'id': 11, 'order_no': 'EO-11', 'stage': 'shipping', 'project_name': '整套委外', 'secret': 'hidden'},
                {'id': 99, 'order_no': 'EO-OTHER', 'stage': 'shipping'},
            ]})
        if path.endswith('/entrust/fulfillment/product-shipment/list'):
            return httpx.Response(200, json={'code': 200, 'data': [{
                'id': 21, 'order_id': 11, 'order_no': 'EO-11', 'shipment_no': 'PS-11',
                'logistics_company': '顺达物流', 'tracking_no': 'TRACK-11',
                'arrival_status': 'pending',
                'lines': [{
                    'id': 31, 'part_no': 'P-1', 'mold_no': 'M-001',
                    'receipt_status': 'pending', 'private': 'hidden',
                }],
                'qr_token': 'hidden',
            }]})
        if path.endswith('/entrust/exception/list'):
            return httpx.Response(200, json={'code': 200, 'data': {'rows': [
                {'id': 5, 'order_id': 11, 'order_no': 'EO-11', 'status': 'open', 'description': '待处理', 'token': 'hidden'},
            ]}})
        return httpx.Response(404, json={'code': 404})

    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))
    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        result = client.outsource_execution_context(mold_no='M-001', project_no='P-001')
    finally:
        client.close()

    assert result['totals'] == {
        'projects': 1, 'production_orders': 1, 'fulfillment_orders': 1,
        'product_shipments': 1, 'exceptions': 1,
    }
    assert result['production_records'][0]['source_ref'] == 'entrust/production/list:11'
    assert result['fulfillment_records'][0]['order_no'] == 'EO-11'
    assert result['product_shipment_records'][0]['shipment_no'] == 'PS-11'
    assert result['product_shipment_records'][0]['tracking_no'] == 'TRACK-11'
    assert result['product_shipment_records'][0]['lines'][0]['part_no'] == 'P-1'
    assert result['exception_records'][0]['status'] == 'open'
    assert 'secret' not in str(result)
    assert 'internal_cost' not in str(result)
    assert 'qr_token' not in str(result)
    assert 'private' not in str(result)
    assert [path for _, path, _ in calls] == [
        '/entrust/project/list', '/entrust/production/list', '/entrust/fulfillment/orders',
        '/entrust/fulfillment/product-shipment/list', '/entrust/exception/list',
    ]


def test_business_molds_reads_unwrapped_project_mold_candidates_without_sensitive_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={'molds': [
            {'project_code': 'P-001', 'mold_code': 'M-001', 'overall_progress': 40,
             'private': 'hidden'},
            {'project_code': 'P-OTHER', 'mold_code': 'M-OTHER'},
        ]})

    client = erp_adapter.ERPClient(
        token='erp-token',
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.business_molds(project_no='P-001')
    finally:
        client.close()

    assert result['totals'] == {'molds': 2}
    assert result['records'][0]['project_code'] == 'P-001'
    assert result['records'][0]['source_ref'].endswith(':P-001:M-001')
    assert 'private' not in str(result)
    assert calls == [('/scheduling/api/business/molds/', {'project': 'P-001', 'mold': ''})]


def test_manufacturing_execution_context_accepts_unwrapped_scheduling_read_payloads(monkeypatch):
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))
    def handler(request: httpx.Request):
        if request.url.path.endswith('/scheduling/api/manufacturing-orders/'):
            return httpx.Response(200, json={'rows': [{
                'reference': 'MO-1', 'project_id': 'P-001', 'operation_id': '装配',
                'status': 'running', 'quantity_completed': 2, 'secret': 'hidden',
            }]})
        if request.url.path.endswith('/scheduling/api/work-reports/'):
            return httpx.Response(200, json={'success': True, 'rows': [{
                'id': 3, 'order_ref': 'MO-1', 'operation_name': '装配',
                'work_hours': 4, 'worker_name': '张三', 'private': 'hidden',
            }]})
        if request.url.path.endswith('/scheduling/api/work-order-reports/'):
            return httpx.Response(200, json={'success': True, 'rows': [{
                'id': 4, 'order_no': 'MO-1', 'report_type': 'FINISH',
                'progress': 100, 'private': 'hidden',
            }]})
        if request.url.path.endswith('/quality/inspection/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [{
                'id': 5, 'inspectionNo': 'QC-1', 'moldNo': 'M-1', 'projectNo': 'P-001',
                'status': 'completed', 'result': 'qualified',
                'details': [{'id': 6, 'materialNo': 'P-1', 'token': 'hidden'}],
            }]})
        return httpx.Response(404, json={'detail': 'not found'})

    client = erp_adapter.ERPClient(
        token='erp-token',
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.manufacturing_execution_context(project_no='P-001')
    finally:
        client.close()

    assert result['totals'] == {
        'manufacturing_orders': 1, 'work_reports': 1, 'work_order_reports': 1,
        'quality_inspections': 1,
    }
    assert result['manufacturing_orders'][0]['source_ref'] == 'scheduling/api/manufacturing-orders/:MO-1'
    assert result['work_reports'][0]['work_hours'] == 4
    assert result['quality_inspections'][0]['inspectionNo'] == 'QC-1'
    assert result['quality_inspections'][0]['details'][0]['materialNo'] == 'P-1'
    assert 'secret' not in str(result)
    assert 'private' not in str(result)
    assert 'token' not in str(result)


def test_quality_permission_gap_does_not_hide_manufacturing_facts(monkeypatch):
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        if request.url.path.endswith('/scheduling/api/manufacturing-orders/'):
            return httpx.Response(200, json={'rows': [{'reference': 'MO-1', 'project_id': 'P-001'}]})
        if request.url.path.endswith('/scheduling/api/work-reports/'):
            return httpx.Response(200, json={'rows': []})
        if request.url.path.endswith('/scheduling/api/work-order-reports/'):
            return httpx.Response(200, json={'rows': []})
        if request.url.path.endswith('/quality/inspection/list'):
            return httpx.Response(403, json={'code': 403})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        result = client.manufacturing_execution_context(project_no='P-001')
    finally:
        client.close()

    assert result['totals']['manufacturing_orders'] == 1
    assert result['totals']['quality_inspections'] == 0
    assert result['quality_status'] == 'ERP_FORBIDDEN'
    assert '质检查询未完成' in ''.join(result['limitations'])


def test_contract_finance_context_reads_payment_plans_and_records_by_contract_number(monkeypatch):
    calls = []

    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        path = request.url.path
        if path.endswith('/system/contract/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [{
                'id': 31, 'contractNo': 'SC-001', 'contractName': '销售合同',
                'contractAmount': '1000.00', 'secret': 'hidden',
            }]})
        if path.endswith('/system/contract/paymentPlan/list/31'):
            return httpx.Response(200, json={'code': 200, 'data': [{
                'id': 41, 'contractId': 31, 'contractNo': 'SC-001',
                'paymentStage': '验收款', 'paymentAmount': '300.00',
                'private': 'hidden',
            }]})
        if path.endswith('/system/contract/paymentRecord/list/31'):
            return httpx.Response(200, json={'code': 200, 'data': [{
                'id': 51, 'paymentNo': 'PAY-001', 'contractId': 31,
                'contractNo': 'SC-001', 'paymentAmount': '300.00',
                'paymentDate': '2026-09-23', 'token': 'hidden',
                'invoiceStatus': '已开票', 'invoiceNo': 'INV-001',
            }]})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(
        token='erp-token',
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.contract_finance_context(['SC-001', 'MISSING'])
    finally:
        client.close()

    assert result['totals'] == {'contracts': 1, 'payment_plans': 1, 'payment_records': 1}
    assert result['contract_records'][0]['source_ref'] == 'system/contract/list:31'
    assert result['payment_plan_records'][0]['paymentStage'] == '验收款'
    assert result['payment_records'][0]['paymentNo'] == 'PAY-001'
    assert result['payment_records'][0]['invoiceStatus'] == '已开票'
    assert result['payment_records'][0]['invoiceNo'] == 'INV-001'
    assert 'secret' not in str(result)
    assert 'private' not in str(result)
    assert 'token' not in str(result)
    assert calls == [
        '/system/contract/list',
        '/system/contract/list',
        '/system/contract/paymentPlan/list/31',
        '/system/contract/paymentRecord/list/31',
    ]


def test_procurement_execution_context_reads_project_scoped_erp_facts(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        path = request.url.path
        if path.endswith('/purchase/order/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 61, 'orderNo': 'PO-1', 'projectNo': 'P-001', 'moldNo': 'M-1',
                 'status': 3, 'pendingDeliveryQuantity': 2, 'private': 'hidden'},
                {'id': 62, 'orderNo': 'PO-OTHER', 'projectNo': 'P-OTHER', 'moldNo': 'M-X'},
            ]})
        if path.endswith('/purchase/supplier-delivery/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 71, 'deliveryNo': 'SD-1', 'purchaseOrderNo': 'PO-1',
                 'moldNo': 'M-1', 'deliveryQty': 2, 'secret': 'hidden'},
                {'id': 72, 'deliveryNo': 'SD-X', 'purchaseOrderNo': 'PO-OTHER', 'moldNo': 'M-X'},
            ]})
        if path.endswith('/material/inbound/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 81, 'inboundNo': 'IN-1', 'orderNo': 'PO-1', 'moldNo': 'M-1',
                 'inspectResult': 1, 'token': 'hidden'},
                {'id': 82, 'inboundNo': 'IN-X', 'orderNo': 'PO-OTHER', 'moldNo': 'M-X'},
            ]})
        if path.endswith('/material/stock-flow/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 91, 'flowNo': 'SF-1', 'orderNo': 'PO-1', 'moldNo': 'M-1',
                 'quantity': -2, 'private': 'hidden'},
                {'id': 92, 'flowNo': 'SF-X', 'orderNo': 'PO-OTHER', 'moldNo': 'M-X'},
            ]})
        if path.endswith('/quality/inspection/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 101, 'inspectionNo': 'QC-1', 'inboundNo': 'IN-1',
                 'moldNo': 'M-1', 'status': 'completed', 'result': 'qualified'},
                {'id': 102, 'inspectionNo': 'QC-X', 'inboundNo': 'IN-X',
                 'moldNo': 'M-X', 'status': 'completed', 'result': 'qualified'},
            ]})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        result = client.procurement_execution_context(mold_no='M-1', project_no='P-001')
    finally:
        client.close()

    assert result['totals'] == {
        'purchase_orders': 1, 'supplier_deliveries': 1, 'inbounds': 1, 'stock_flows': 1,
        'quality_inspections': 1,
    }
    assert result['purchase_order_records'][0]['orderNo'] == 'PO-1'
    assert result['supplier_delivery_records'][0]['source_ref'].endswith(':71')
    assert result['supplier_delivery_records'][0]['deliveryNo'] == 'SD-1'
    assert result['inbound_records'][0]['inboundNo'] == 'IN-1'
    assert result['stock_flow_records'][0]['quantity'] == -2
    assert result['quality_inspection_records'][0]['inspectionNo'] == 'QC-1'
    assert 'private' not in str(result)
    assert 'secret' not in str(result)
    assert 'token' not in str(result)
    assert calls == [
        '/purchase/order/list',
        '/purchase/supplier-delivery/list',
        '/material/inbound/list',
        '/material/stock-flow/list',
        '/quality/inspection/list',
    ]


def test_procurement_migration_adapter_uses_registered_decision_and_delivery_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, request.read().decode() if request.content else ''))
        path = request.url.path
        if path.endswith('/purchase/decision/list'):
            return httpx.Response(200, json={'code': 200, 'data': [{'groupId': 7, 'groupNo': 'G-7', 'projectNo': 'P-1', 'moldNo': 'M-1', 'materialCategory': 'hardware'}]})
        if path.endswith('/purchase/decision/7'):
            return httpx.Response(200, json={'code': 200, 'data': {'groupId': 7, 'projectNo': 'P-1', 'moldNo': 'M-1', 'groupStatus': 'READY', 'version': 2}})
        if path.endswith('/quote-approval-preview'):
            return httpx.Response(200, json={'code': 200, 'data': {'groupId': 7, 'items': []}})
        if path.endswith('/hardware-quote'):
            return httpx.Response(200, json={'code': 200, 'data': {'accepted': True}})
        if path.endswith('/confirm'):
            return httpx.Response(200, json={'code': 200, 'data': {'confirmed': True}})
        if path.endswith('/create-order'):
            return httpx.Response(200, json={'code': 200, 'data': {'orderNo': 'PO-7'}})
        if path.endswith('/supplier-delivery/9/modify-request'):
            return httpx.Response(200, json={'code': 200, 'data': {'requestId': 99}})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        assert client.purchase_decision_groups(project_no='P-1')[0]['groupNo'] == 'G-7'
        assert client.group(7)['groupStatus'] == 'READY'
        assert client.quote_approval_preview(7)['groupId'] == 7
        assert client.submit_hardware_quote(7, {'supplierId': 1})['accepted'] is True
        assert client.confirm_purchase_decision(7, {'decision': 'CONFIRM'})['confirmed'] is True
        assert client.create_purchase_order(7)['orderNo'] == 'PO-7'
        assert client.create_supplier_delivery_modify_request(9, {'reason': '延期'})['requestId'] == 99
    finally:
        client.close()

    assert [path for _, path, _ in calls] == [
        '/purchase/decision/list', '/purchase/decision/7',
        '/purchase/decision/7/quote-approval-preview',
        '/purchase/decision/7/hardware-quote', '/purchase/decision/7/confirm',
        '/purchase/decision/7/create-order', '/purchase/supplier-delivery/9/modify-request',
    ]


def test_phase2_adapter_uses_workbench_and_supplier_portal_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith('/purchase/request/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [{'id': 1, 'requestNo': 'PR-1', 'secret': 'hidden'}]})
        if path.endswith('/purchase/request/1'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 1, 'version': 2}})
        if path.endswith('/purchase/workbench/split/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [{'id': 2, 'requestNo': 'PR-1'}]})
        if path.endswith('/purchase/workbench/split/1'):
            return httpx.Response(200, json={'code': 200, 'data': {'requestId': 1, 'groups': []}})
        if path.endswith('/supplier/quote-task/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [{'id': 3, 'taskNo': 'QT-3'}]})
        if path.endswith('/supplier/quote-task/3'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 3, 'version': 4}})
        if path.endswith('/supplier/purchase-order/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [{'id': 4, 'orderNo': 'PO-4'}]})
        if path.endswith('/supplier/purchase-order/4'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 4, 'version': 5}})
        if path.endswith('/supplier/delivery/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [{'id': 5, 'deliveryNo': 'SD-5'}]})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        assert client.purchase_request_list()[0]['requestNo'] == 'PR-1'
        assert client.purchase_request_detail(1)['version'] == 2
        assert client.purchase_workbench_split_list()[0]['requestNo'] == 'PR-1'
        assert client.purchase_workbench_split_detail(1)['requestId'] == 1
        context = client.supplier_portal_context({'moldNo': 'M-1'})
    finally:
        client.close()
    assert context['quote_tasks'][0]['taskNo'] == 'QT-3'
    assert context['purchase_orders'][0]['orderNo'] == 'PO-4'
    assert context['deliveries'][0]['deliveryNo'] == 'SD-5'
    assert 'secret' not in str(context)
    assert calls == [
        ('GET', '/purchase/request/list'), ('GET', '/purchase/request/1'),
        ('GET', '/purchase/workbench/split/list'), ('GET', '/purchase/workbench/split/1'),
        ('GET', '/supplier/quote-task/list'), ('GET', '/supplier/purchase-order/list'),
        ('GET', '/supplier/delivery/list'),
    ]


def test_phase3_adapter_uses_split_adjustment_routes_and_filters_history(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        path = request.url.path
        if path.endswith('/purchase/workbench/split-adjustments/context/8'):
            return httpx.Response(200, json={'code': 200, 'data': {'requestId': 8, 'version': 3}})
        if path.endswith('/purchase/workbench/split-adjustments'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 12, 'adjustmentNo': 'ADJ-12', 'status': 'DRAFT', 'secret': 'hidden'},
            ]})
        if path.endswith('/purchase/workbench/split-adjustments/12'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 12, 'version': 4}})
        if path.endswith('/purchase/workbench/split-adjustments/preview'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 12, 'status': 'PREVIEWED'}})
        if path.endswith('/purchase/workbench/split-adjustments/12/submit'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 12, 'status': 'SUBMITTED'}})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        assert client.purchase_split_adjustment_context(8)['version'] == 3
        history = client.purchase_split_adjustment_history({'requestId': 8})
        assert history[0]['adjustmentNo'] == 'ADJ-12'
        assert 'secret' not in str(history)
        assert client.purchase_split_adjustment_detail(12)['version'] == 4
        assert client.preview_purchase_split_adjustment({'splitGroupId': 7})['status'] == 'PREVIEWED'
        assert client.submit_purchase_split_adjustment(12, {'expectedVersion': 4})['status'] == 'SUBMITTED'
    finally:
        client.close()
    assert [(method, path) for method, path, _ in calls] == [
        ('GET', '/purchase/workbench/split-adjustments/context/8'),
        ('GET', '/purchase/workbench/split-adjustments'),
        ('GET', '/purchase/workbench/split-adjustments/12'),
        ('POST', '/purchase/workbench/split-adjustments/preview'),
        ('POST', '/purchase/workbench/split-adjustments/12/submit'),
    ]


def test_phase3_adapter_uses_temporary_group_and_repurchase_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith('/temporary-groups'):
            return httpx.Response(200, json={'code': 200, 'data': {'clientGroupKey': 'A'}})
        if '/temporary-groups/' in path:
            return httpx.Response(200, json={'code': 200, 'data': {}})
        if path.endswith('/purchase/manual-dispatch/by-order/19'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 29, 'version': 2}})
        if path.endswith('/purchase/manual-dispatch/29'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 29, 'version': 2}})
        if path.endswith('/purchase/manual-dispatch/suppliers'):
            return httpx.Response(200, json={'code': 200, 'rows': [{'id': 31, 'name': '供应商A', 'secret': 'hidden'}]})
        if path.endswith('/purchase/manual-dispatch/29/draft'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 29, 'status': 'DRAFT'}})
        if path.endswith('/purchase/manual-dispatch/29/submit'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 29, 'status': 'APPROVAL_PENDING'}})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        assert client.save_purchase_temporary_group(8, {'clientGroupKey': 'A'})['clientGroupKey'] == 'A'
        client.delete_purchase_temporary_group(8, 'A')
        assert client.manual_dispatch_by_order(19)['id'] == 29
        assert client.manual_dispatch_detail(29)['version'] == 2
        suppliers = client.manual_dispatch_suppliers()
        assert suppliers[0]['name'] == '供应商A'
        assert 'secret' not in str(suppliers)
        assert client.save_manual_dispatch_draft(29, {})['status'] == 'DRAFT'
        assert client.submit_manual_dispatch(29)['status'] == 'APPROVAL_PENDING'
    finally:
        client.close()
    assert [(method, path) for method, path in calls] == [
        ('PUT', '/purchase/workbench/split-adjustments/context/8/temporary-groups'),
        ('DELETE', '/purchase/workbench/split-adjustments/context/8/temporary-groups/A'),
        ('GET', '/purchase/manual-dispatch/by-order/19'),
        ('GET', '/purchase/manual-dispatch/29'),
        ('GET', '/purchase/manual-dispatch/suppliers'),
        ('PUT', '/purchase/manual-dispatch/29/draft'),
        ('POST', '/purchase/manual-dispatch/29/submit'),
    ]


def test_phase3_adapter_uses_erp_agent_supplier_ranking_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/supplier-ranking'):
            return httpx.Response(200, json={'code': 200, 'data': {'groupId': 7, 'snapshotHash': 'a' * 64}})
        if request.url.path.endswith('/supplier-rank-adjustment/preview'):
            return httpx.Response(200, json={'code': 200, 'data': {'sourceSnapshotHash': 'a' * 64}})
        if request.url.path.endswith('/supplier-rank-adjustment/proposals'):
            return httpx.Response(200, json={'code': 200, 'data': {'proposalId': 12}})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        assert client.purchase_supplier_ranking(7)['snapshotHash'] == 'a' * 64
        assert client.preview_supplier_rank_adjustment({'splitGroupId': 7})['sourceSnapshotHash'] == 'a' * 64
        assert client.create_supplier_rank_adjustment_proposal({'splitGroupId': 7})['proposalId'] == 12
    finally:
        client.close()
    assert calls == [
        ('GET', '/api/agent/procurement/split-groups/7/supplier-ranking'),
        ('POST', '/api/agent/procurement/actions/supplier-rank-adjustment/preview'),
        ('POST', '/api/agent/procurement/actions/supplier-rank-adjustment/proposals'),
    ]


def test_phase4_adapter_uses_quantity_change_and_hardware_award_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/quantity-change-impact'):
            return httpx.Response(200, json={'code': 200, 'data': {'orderId': 8, 'version': 2}})
        if request.url.path.endswith('/order-quantity-change-proposals'):
            return httpx.Response(200, json={'code': 200, 'data': {'proposalId': 15}})
        if request.url.path.endswith('/purchase/hardware-award/11'):
            return httpx.Response(200, json={'code': 200, 'data': {'batchId': 11, 'version': 3}})
        if request.url.path.endswith('/purchase/hardware-award/11/submit'):
            return httpx.Response(200, json={'code': 200, 'data': {'status': 'PENDING'}})
        if request.url.path.endswith('/final-preview'):
            return httpx.Response(200, json={'code': 200, 'data': {'valid': True}})
        if request.url.path.endswith('/final-approve'):
            return httpx.Response(200, json={'code': 200, 'data': {'status': 'APPROVED'}})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        assert client.purchase_order_quantity_change_impact(8)['version'] == 2
        assert client.create_order_quantity_change_proposal({})['proposalId'] == 15
        assert client.hardware_award_detail(11)['batchId'] == 11
        assert client.hardware_award_submit(11, {})['status'] == 'PENDING'
        assert client.hardware_award_final_preview(11, {})['valid'] is True
        assert client.hardware_award_final_approve(11, {})['status'] == 'APPROVED'
    finally:
        client.close()
    assert calls == [
        ('GET', '/api/agent/procurement/orders/8/quantity-change-impact'),
        ('POST', '/api/agent/procurement/actions/order-quantity-change-proposals'),
        ('GET', '/purchase/hardware-award/11'),
        ('POST', '/purchase/hardware-award/11/submit'),
        ('POST', '/purchase/hardware-award/todos/11/final-preview'),
        ('POST', '/purchase/hardware-award/todos/11/final-approve'),
    ]


def test_phase4_adapter_uses_price_compare_and_sensitive_price_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/price-compare/list'):
            return httpx.Response(200, json={'code': 200, 'data': [{'priceId': 7}]})
        if request.url.path.endswith('/negotiated-approval'):
            return httpx.Response(200, json={'code': 200, 'data': {'approvalCount': 1}})
        if request.url.path.endswith('/system-price'):
            return httpx.Response(200, json={'code': 200, 'data': {'unitPrice': '8.8'}})
        if request.url.path.endswith('/price-access/policy'):
            return httpx.Response(200, json={'code': 200, 'data': {'allowed': True}})
        if request.url.path.endswith('/price-access/requests/4'):
            return httpx.Response(200, json={'code': 200, 'data': {'id': 4, 'status': 'pending'}})
        if request.url.path.endswith('/price-access/requests/4/decision'):
            return httpx.Response(200, json={'code': 200, 'data': {'status': 'approved'}})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        assert client.purchase_price_compare_list()[0]['priceId'] == 7
        assert client.submit_purchase_price_compare_negotiated_approval({})['approvalCount'] == 1
        assert client.manual_dispatch_system_price(2, 3, 4)['unitPrice'] == '8.8'
        assert client.supplier_price_access_policy({})['allowed'] is True
        assert client.supplier_price_access_request(4)['status'] == 'pending'
        assert client.decide_supplier_price_access_request(4, {})['status'] == 'approved'
    finally:
        client.close()
    assert calls == [
        ('GET', '/purchase/price-compare/list'),
        ('POST', '/purchase/price-compare/negotiated-approval'),
        ('GET', '/purchase/manual-dispatch/2/lines/3/system-price'),
        ('POST', '/api/agent/procurement/price-access/policy'),
        ('GET', '/api/agent/procurement/price-access/requests/4'),
        ('POST', '/api/agent/procurement/price-access/requests/4/decision'),
    ]


def test_plan_progress_rejects_success_payload_without_data_instead_of_raising_keyerror(monkeypatch):
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        return httpx.Response(200, json={'code': 200, 'msg': '暂无项目节点'})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DomainError) as error:
            client.project_nodes(mold_no='M-001', project_no='P-001')
    finally:
        client.close()

    assert error.value.code == 'ERP_PROTOCOL_ERROR'
    assert '缺少 data' in error.value.message


def test_plan_progress_uses_project_id_endpoint_and_accepts_erp_rows_envelopes(monkeypatch):
    calls = []
    monkeypatch.setattr(erp_adapter, 'settings', lambda: SimpleNamespace(
        erp_base_url='https://erp.example.test', erp_allow_insecure_local=False,
        credential_encryption_key='unused',
    ))

    def handler(request: httpx.Request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith('/system/project/list'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 22, 'projectNo': 'P-001', 'projectName': '模具项目'},
            ]})
        if request.url.path.endswith('/system/projectNode/project/22'):
            return httpx.Response(200, json={'code': 200, 'rows': [
                {'id': 127, 'projectId': 22, 'nodeCode': 'DESIGN', 'nodeName': '设计', 'status': 0},
            ]})
        if request.url.path.endswith('/system/productionSchedule/list'):
            return httpx.Response(200, json={'code': 200, 'rows': []})
        return httpx.Response(404, json={'code': 404})

    client = erp_adapter.ERPClient(token='erp-token', transport=httpx.MockTransport(handler))
    try:
        result = client.plan_execution_progress(project_no='P-001')
    finally:
        client.close()

    assert result['totals'] == {'project_nodes': 1, 'production_schedules': 0}
    assert result['project_nodes'][0]['nodeName'] == '设计'
    assert calls == [
        ('/system/project/list', {'projectNo': 'P-001', 'pageNum': '1', 'pageSize': '50'}),
        ('/system/projectNode/project/22', {}),
        ('/system/productionSchedule/list', {'projectNo': 'P-001'}),
    ]
