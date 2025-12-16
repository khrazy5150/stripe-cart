// ======== ORDERS MANAGEMENT ========

let allOrders = [];
let filteredOrders = [];
let ordersNextKey = null;
let ordersHasMore = true;
let ordersIsLoading = false;
let totalUnfulfilledCount = 0;

// ======== LOAD ORDERS ========

async function loadOrdersForClient() {
    const clientID = window.currentClientID ? window.currentClientID() : null;
    if (!clientID || ordersIsLoading) return;
    
    ordersIsLoading = true;
    showOrdersLoading();
    
    try {
        const params = new URLSearchParams({ limit: '10' });
        if (clientID) params.set('clientID', clientID);
        if (ordersNextKey) params.set('lastKey', ordersNextKey);
        
        const response = await fetch(`${CONFIG.apiBase}/orders?${params.toString()}`, { 
            method: 'GET'
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        
        if (data.totalCount !== undefined) {
            totalUnfulfilledCount = data.totalCount;
        }
        
        if (ordersNextKey) {
            allOrders = allOrders.concat(data.orders || []);
        } else {
            allOrders = data.orders || [];
        }
        
        ordersNextKey = data.nextKey || null;
        ordersHasMore = !!data.hasMore;
        
        filterOrders();
        updateOrdersStats();
        updateLoadMoreButton();
    } catch (err) {
        console.error('Orders loading error:', err);
        showOrderError('Failed to load orders. Please try again.');
    } finally {
        ordersIsLoading = false;
    }
}

async function loadMoreOrders() {
    const clientID = window.currentClientID ? window.currentClientID() : null;
    if (!ordersHasMore || ordersIsLoading || !clientID) {
        disableLoadMoreButton();
        return;
    }
    await loadOrdersForClient();
}

// ======== FILTER ORDERS ========

function filterOrders() {
    const customerSearch = document.getElementById('search-customer').value.toLowerCase();
    const productSearch = document.getElementById('search-product').value.toLowerCase();
    const showFulfilled = document.getElementById('toggle-fulfilled').checked;

    filteredOrders = (allOrders || []).filter(order => {
        const name = (order.customer_name || '').toLowerCase();
        const email = (order.customer_email || '').toLowerCase();
        const prod = (order.product_name || '').toLowerCase();

        const customerMatch = !customerSearch || name.includes(customerSearch) || email.includes(customerSearch);
        const productMatch = !productSearch || prod.includes(productSearch);

        const isFulfilled = (order.fulfilled === true || order.fulfilled === 'true');
        const paid = (order.payment_status || '').toLowerCase() === 'succeeded';

        const statusMatch = paid && (showFulfilled ? true : !isFulfilled);

        return customerMatch && productMatch && statusMatch;
    });

    renderOrders();
    updateOrdersStats();
}

// ======== RENDER ORDERS ========

function renderOrders() {
    const list = document.getElementById('orders-list');

    if (filteredOrders.length === 0) {
        list.innerHTML = `
            <div class="no-orders">
                <h3>No orders need fulfillment</h3>
                <p>Everything paid is shipped—or your search filters are too narrow.</p>
            </div>`;
        return;
    }

    list.innerHTML = filteredOrders.map(order => {
        const isFulfilled = (order.fulfilled === true || order.fulfilled === 'true');
        const paid = (order.payment_status || '').toLowerCase() === 'succeeded';
        const badge = !paid
            ? `<span class="order-badge badge-warn">Payment Pending</span>`
            : isFulfilled
                ? `<span class="order-badge badge-success">Fulfilled</span>`
                : `<span class="order-badge badge-danger">Needs Fulfillment</span>`;

        const shipAddr = order.shipping_address && order.shipping_address !== 'N/A' ? order.shipping_address : 'N/A';
        
        const hasTracking = order.tracking_number && order.tracking_number !== 'N/A';
        const trackingInfo = hasTracking ? `
            <div class="detail-value" style="margin-top: 8px;">
                <strong>Tracking:</strong> ${escapeHtml(order.tracking_number)}<br>
                ${order.tracking_url ? `<a href="${escapeHtml(order.tracking_url)}" target="_blank" style="color: var(--brand);">Track Package</a>` : ''}
            </div>
        ` : '';

        return `
            <div class="order-card" data-order-id="${order.order_id}">
                <div class="order-header">
                    <div class="order-id">Order #${escapeHtml(order.order_id)}</div>
                    <div class="order-date">${escapeHtml(order.order_date)} &nbsp; ${badge}</div>
                </div>

                <div class="order-details">
                    <div class="detail-group">
                        <div class="detail-label">Customer</div>
                        <div class="detail-value">${escapeHtml(order.customer_name || 'N/A')}</div>
                        <div class="detail-value">${escapeHtml(order.customer_email || 'N/A')}</div>
                        <div class="detail-value">${escapeHtml(order.customer_phone || 'N/A')}</div>
                    </div>

                    <div class="detail-group">
                        <div class="detail-label">Product</div>
                        <div class="detail-value product-name">${escapeHtml(order.product_name || 'N/A')}</div>
                        <div class="detail-value amount">${formatCurrency(order.amount, order.currency)}</div>
                    </div>

                    <div class="detail-group">
                        <div class="detail-label">Shipping Address</div>
                        <div class="detail-value">${escapeHtml(shipAddr)}</div>
                        ${trackingInfo}
                    </div>

                    <div class="detail-group">
                        <div class="detail-label">Actions</div>
                        ${!hasTracking && paid && !isFulfilled ? `
                            <button class="fulfill-btn" style="background: var(--brand); margin-bottom: 8px;" onclick="createShippingLabel('${order.order_id}')">
                                Create Label
                            </button>
                        ` : ''}
                        <button class="fulfill-btn" ${!paid || isFulfilled ? 'disabled' : ''} onclick="fulfillOrder('${order.order_id}')">
                            ${isFulfilled ? 'Already Fulfilled' : 'Mark as Fulfilled'}
                        </button>
                    </div>
                </div>
            </div>`;
    }).join('');
}

// ======== FULFILL ORDER ========

async function fulfillOrder(orderId) {
    if (!confirm('Mark this order as fulfilled? This will send a confirmation email to the customer.')) return;

    const btn = document.querySelector(`[data-order-id="${orderId}"] .fulfill-btn[onclick*="fulfillOrder"]`);
    const originalText = btn.textContent;
    
    try {
        btn.disabled = true;
        btn.textContent = 'Processing...';
        
        // Send email
        await authedFetch('/admin/send-fulfillment-email', {
            method: 'POST',
            body: { order_id: orderId, manual: true }
        });
        
        // Mark fulfilled
        const response = await fetch(`${CONFIG.apiBase}/orders/${encodeURIComponent(orderId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fulfilled: true })
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        allOrders = allOrders.map(o => o.order_id === orderId ? { ...o, fulfilled: true } : o);
        filterOrders();
        showOrderSuccess('Order marked as fulfilled and customer notified!');
    } catch (err) {
        console.error('Fulfill order error:', err);
        showOrderError('Failed to mark order as fulfilled. Please try again.');
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ======== CREATE SHIPPING LABEL ========

async function createShippingLabel(orderId) {
    const btn = document.querySelector(`[data-order-id="${orderId}"] .fulfill-btn[onclick*="createShippingLabel"]`);
    if (!btn) return;
    
    const originalText = btn.textContent;
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span style="display: inline-flex; align-items: center; gap: 8px;"><span class="button-spinner"></span>Fetching rates...</span>';
        
        const clientID = document.getElementById('clientID').value.trim();
        const ratesRes = await authedFetch('/admin/get-rates', {
            method: 'POST',
            body: { order_id: orderId }
        });
        
        btn.disabled = false;
        btn.textContent = originalText;
        
        if (!ratesRes.ok || !ratesRes.body.rates || ratesRes.body.rates.length === 0) {
            showOrderError('Failed to get shipping rates');
            return;
        }
        
        const rates = ratesRes.body.rates;
        const defaultRate = rates.reduce((prev, curr) => 
            parseFloat(curr.rate) < parseFloat(prev.rate) ? curr : prev
        );
        
        window.showShippingConfirmation(orderId, defaultRate, rates);
        
    } catch (err) {
        console.error('Create label error:', err);
        showOrderError('Failed to initiate label creation');
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ======== UPDATE STATS ========

function updateOrdersStats() {
    const pending = allOrders.filter(o => 
        (o.payment_status || '').toLowerCase() === 'succeeded' && 
        !(o.fulfilled === true || o.fulfilled === 'true')
    );
    const total = pending.reduce((sum, o) => sum + (o.amount || 0), 0);
    const avg = pending.length ? total / pending.length : 0;

    const displayCount = totalUnfulfilledCount > 0 ? totalUnfulfilledCount : pending.length;
    
    document.getElementById('total-orders').textContent = displayCount;
    document.getElementById('total-revenue').textContent = formatCurrency(total, 'usd');
    document.getElementById('avg-order').textContent = formatCurrency(avg, 'usd');
}

function updateLoadMoreButton() {
    const container = document.getElementById('load-more-container');
    const btn = document.getElementById('load-more-btn');
    
    if (ordersHasMore && !ordersIsLoading) { 
        container.style.display = 'block'; 
        btn.disabled = false; 
        btn.textContent = 'Load More Orders'; 
    } else if (ordersIsLoading) { 
        container.style.display = 'block'; 
        btn.disabled = true; 
    } else { 
        container.style.display = 'none'; 
    }
}

function disableLoadMoreButton() {
    const container = document.getElementById('load-more-container');
    if (container) container.style.display = 'none';
}

// ======== UTILITY FUNCTIONS ========

function clearOrderFilters() {
    document.getElementById('search-customer').value = '';
    document.getElementById('search-product').value = '';
    document.getElementById('toggle-fulfilled').checked = false;
    filterOrders();
}

function refreshOrders() {
    allOrders = [];
    filteredOrders = [];
    ordersNextKey = null;
    ordersHasMore = true;
    document.getElementById('orders-list').innerHTML = '<div class="loading-spinner"></div>';
    const clientID = window.currentClientID ? window.currentClientID() : null;
    if (clientID) {
        loadOrdersForClient();
    }
}

function resetOrdersData() {
    allOrders = [];
    filteredOrders = [];
    ordersNextKey = null;
    ordersHasMore = true;
    ordersIsLoading = false;
}

function showOrdersLoading() {
    if (allOrders.length === 0) {
        document.getElementById('orders-list').innerHTML = '<div class="loading-spinner"></div>';
    }
}

function showOrderError(message) {
    const container = document.getElementById('order-error-container');
    container.innerHTML = `<div class="message error">${escapeHtml(message)}</div>`;
    setTimeout(() => container.innerHTML = '', 4000);
}

function showOrderSuccess(message) {
    const container = document.getElementById('order-success-container');
    container.innerHTML = `<div class="message success">${escapeHtml(message)}</div>`;
    setTimeout(() => container.innerHTML = '', 3000);
}

// ======== EVENT LISTENERS ========

function setupOrdersListeners() {
    document.getElementById('search-customer').addEventListener('input', filterOrders);
    document.getElementById('search-product').addEventListener('input', filterOrders);
    document.getElementById('toggle-fulfilled').addEventListener('change', filterOrders);
    
    const loadMoreBtn = document.getElementById('load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', loadMoreOrders);
    }
}

// Export functions
window.loadOrdersForClient = loadOrdersForClient;
window.fulfillOrder = fulfillOrder;
window.createShippingLabel = createShippingLabel;
window.clearOrderFilters = clearOrderFilters;
window.refreshOrders = refreshOrders;
window.resetOrdersData = resetOrdersData;
window.setupOrdersListeners = setupOrdersListeners;

if (window.setupOrdersListeners) {
    window.setupOrdersListeners();
}