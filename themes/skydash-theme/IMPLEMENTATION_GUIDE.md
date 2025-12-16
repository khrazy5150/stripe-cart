# SkyDash Theme Implementation Guide

## Overview
This is a complete, production-ready CSS theme system inspired by the SkyDash Bootstrap dashboard template. It includes light and dark mode support with zero external dependencies (no Bootstrap, no Tailwind, no SCSS).

## Features
✅ **Complete Component Library**: Buttons, forms, cards, modals, tables, alerts, badges, dropdowns, tabs, and more  
✅ **Light & Dark Modes**: Automatic theme switching via `data-theme` attribute  
✅ **Pure CSS**: No preprocessors, frameworks, or build tools required  
✅ **Responsive**: Mobile-first design with breakpoints for tablet and desktop  
✅ **CSS Custom Properties**: Easy customization via CSS variables  
✅ **Modern**: Uses CSS Grid, Flexbox, and modern CSS features  
✅ **Accessible**: ARIA-friendly with proper focus states  

## Quick Start

### 1. Include the Theme
```html
<link rel="stylesheet" href="skydash-theme.css">
```

### 2. Toggle Dark Mode
```javascript
// Toggle theme
document.documentElement.setAttribute('data-theme', 'dark');

// Remove dark theme (returns to light)
document.documentElement.removeAttribute('data-theme');

// Toggle function
function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    document.documentElement.setAttribute('data-theme', currentTheme === 'dark' ? 'light' : 'dark');
}
```

## Layout Structure

### App Layout with Sidebar
```html
<div class="app-wrapper">
    <!-- Sidebar -->
    <aside class="sidebar">
        <div class="sidebar-brand">
            <div class="sidebar-brand-icon">
                <img src="logo.png" alt="Logo">
            </div>
            <span class="sidebar-brand-text">Dashboard</span>
        </div>
        
        <nav class="sidebar-nav">
            <div class="nav-section">
                <div class="nav-section-title">Main</div>
                <div class="nav-item">
                    <a href="#" class="nav-link active">
                        <span class="nav-icon">📊</span>
                        <span class="nav-text">Dashboard</span>
                    </a>
                </div>
                <div class="nav-item">
                    <a href="#" class="nav-link">
                        <span class="nav-icon">👥</span>
                        <span class="nav-text">Users</span>
                        <span class="nav-badge">12</span>
                    </a>
                </div>
            </div>
        </nav>
    </aside>
    
    <!-- Main Content -->
    <div class="main-container">
        <header class="header">
            <div class="header-left">
                <button class="sidebar-toggle">☰</button>
                <div class="search-box">
                    <input type="search" class="search-input" placeholder="Search...">
                    <span class="search-icon">🔍</span>
                </div>
            </div>
            <div class="header-right">
                <button class="theme-toggle" onclick="toggleTheme()">🌙</button>
                <div class="user-profile">
                    <div class="user-avatar">JD</div>
                    <div class="user-info">
                        <div class="user-name">John Doe</div>
                        <div class="user-role">Admin</div>
                    </div>
                </div>
            </div>
        </header>
        
        <main class="content">
            <div class="content-wrapper">
                <!-- Your content here -->
            </div>
        </main>
    </div>
</div>
```

## Components

### Cards
```html
<!-- Basic Card -->
<div class="card">
    <div class="card-header">
        <h3 class="card-title">Card Title</h3>
        <div class="card-actions">
            <button class="btn btn-sm btn-primary">Action</button>
        </div>
    </div>
    <div class="card-body">
        Card content goes here
    </div>
    <div class="card-footer">
        Footer content
    </div>
</div>

<!-- Stats Card -->
<div class="stats-card">
    <div class="stats-icon primary">📈</div>
    <div class="stats-content">
        <div class="stats-label">Total Sales</div>
        <div class="stats-value">$24,500</div>
        <div class="stats-change positive">+12.5% from last month</div>
    </div>
</div>
```

### Buttons
```html
<!-- Primary Buttons -->
<button class="btn btn-primary">Primary</button>
<button class="btn btn-secondary">Secondary</button>
<button class="btn btn-success">Success</button>
<button class="btn btn-warning">Warning</button>
<button class="btn btn-danger">Danger</button>
<button class="btn btn-info">Info</button>

<!-- Outline Buttons -->
<button class="btn btn-outline-primary">Outline Primary</button>
<button class="btn btn-outline-success">Outline Success</button>

<!-- Sizes -->
<button class="btn btn-xs btn-primary">Extra Small</button>
<button class="btn btn-sm btn-primary">Small</button>
<button class="btn btn-primary">Default</button>
<button class="btn btn-lg btn-primary">Large</button>
<button class="btn btn-xl btn-primary">Extra Large</button>

<!-- Block Button -->
<button class="btn btn-primary btn-block">Full Width Button</button>

<!-- Icon Button -->
<button class="btn btn-icon btn-primary">🔍</button>

<!-- Button Group -->
<div class="btn-group">
    <button class="btn btn-secondary">Left</button>
    <button class="btn btn-secondary">Middle</button>
    <button class="btn btn-secondary">Right</button>
</div>
```

### Forms
```html
<form>
    <!-- Text Input -->
    <div class="form-group">
        <label class="form-label required">Email</label>
        <input type="email" class="form-input" placeholder="Enter email">
        <span class="form-text">We'll never share your email</span>
    </div>
    
    <!-- Select -->
    <div class="form-group">
        <label class="form-label">Country</label>
        <select class="form-select">
            <option>United States</option>
            <option>Canada</option>
            <option>Mexico</option>
        </select>
    </div>
    
    <!-- Textarea -->
    <div class="form-group">
        <label class="form-label">Message</label>
        <textarea class="form-textarea" rows="4"></textarea>
    </div>
    
    <!-- Checkbox -->
    <div class="form-group">
        <div class="form-check">
            <input type="checkbox" id="terms">
            <label class="form-check-label" for="terms">I agree to terms</label>
        </div>
    </div>
    
    <!-- Radio Buttons -->
    <div class="form-group">
        <div class="form-check">
            <input type="radio" name="plan" id="free">
            <label class="form-check-label" for="free">Free Plan</label>
        </div>
        <div class="form-check">
            <input type="radio" name="plan" id="pro">
            <label class="form-check-label" for="pro">Pro Plan</label>
        </div>
    </div>
    
    <!-- Input Group -->
    <div class="form-group">
        <label class="form-label">Website</label>
        <div class="input-group">
            <span class="input-group-prepend">https://</span>
            <input type="text" class="form-input" placeholder="example.com">
        </div>
    </div>
    
    <!-- Validation States -->
    <div class="form-group">
        <label class="form-label">Valid Input</label>
        <input type="text" class="form-input is-valid" value="Correct!">
        <div class="valid-feedback">Looks good!</div>
    </div>
    
    <div class="form-group">
        <label class="form-label">Invalid Input</label>
        <input type="text" class="form-input is-invalid">
        <div class="invalid-feedback">Please provide a valid input</div>
    </div>
    
    <button type="submit" class="btn btn-primary">Submit</button>
</form>
```

### Tables
```html
<div class="table-wrapper">
    <table class="table">
        <thead>
            <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>John Doe</td>
                <td>john@example.com</td>
                <td>Admin</td>
                <td><span class="badge badge-success">Active</span></td>
            </tr>
            <tr>
                <td>Jane Smith</td>
                <td>jane@example.com</td>
                <td>User</td>
                <td><span class="badge badge-warning">Pending</span></td>
            </tr>
        </tbody>
    </table>
</div>

<!-- Table Variants -->
<table class="table table-striped">...</table>
<table class="table table-bordered">...</table>
<table class="table table-sm">...</table>
```

### Alerts / Messages
```html
<!-- Alert Variants -->
<div class="alert alert-primary">Primary alert message</div>
<div class="alert alert-success">Success alert message</div>
<div class="alert alert-warning">Warning alert message</div>
<div class="alert alert-danger">Danger alert message</div>
<div class="alert alert-info">Info alert message</div>

<!-- Dismissible Alert -->
<div class="alert alert-success alert-dismissible">
    <div class="alert-content">
        Successfully saved!
    </div>
    <button class="alert-close">×</button>
</div>

<!-- Message with Icon -->
<div class="message success">
    <div class="message-icon">✓</div>
    <div class="message-content">
        <div class="message-title">Success!</div>
        Your changes have been saved.
    </div>
</div>
```

### Modals
```html
<!-- Modal Structure -->
<div class="modal" id="myModal">
    <div class="modal-overlay"></div>
    <div class="modal-container">
        <div class="modal-header">
            <h3 class="modal-title">Modal Title</h3>
            <button class="modal-close">×</button>
        </div>
        <div class="modal-body">
            Modal content goes here
        </div>
        <div class="modal-footer">
            <button class="btn btn-secondary">Cancel</button>
            <button class="btn btn-primary">Save Changes</button>
        </div>
    </div>
</div>

<!-- Modal JavaScript -->
<script>
function openModal(modalId) {
    document.getElementById(modalId).classList.add('active');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
}

// Close on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', () => {
        overlay.closest('.modal').classList.remove('active');
    });
});
</script>

<!-- Modal Sizes -->
<div class="modal-container modal-sm">...</div>
<div class="modal-container modal-lg">...</div>
<div class="modal-container modal-xl">...</div>
<div class="modal-container modal-fullscreen">...</div>
```

### Badges
```html
<!-- Badge Variants -->
<span class="badge badge-primary">Primary</span>
<span class="badge badge-success">Success</span>
<span class="badge badge-warning">Warning</span>
<span class="badge badge-danger">Danger</span>
<span class="badge badge-info">Info</span>

<!-- Solid Badges -->
<span class="badge badge-solid-primary">Primary</span>
<span class="badge badge-solid-success">Success</span>
```

### Dropdowns
```html
<div class="dropdown">
    <button class="btn btn-secondary dropdown-toggle" onclick="toggleDropdown(this)">
        Dropdown
    </button>
    <div class="dropdown-menu">
        <div class="dropdown-header">Actions</div>
        <a href="#" class="dropdown-item">Action 1</a>
        <a href="#" class="dropdown-item">Action 2</a>
        <div class="dropdown-divider"></div>
        <a href="#" class="dropdown-item">Settings</a>
    </div>
</div>

<script>
function toggleDropdown(button) {
    button.closest('.dropdown').classList.toggle('open');
}

// Close dropdowns when clicking outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.dropdown')) {
        document.querySelectorAll('.dropdown.open').forEach(d => {
            d.classList.remove('open');
        });
    }
});
</script>
```

### Tabs
```html
<div class="tabs">
    <button class="tab active" onclick="switchTab(event, 'tab1')">Tab 1</button>
    <button class="tab" onclick="switchTab(event, 'tab2')">Tab 2</button>
    <button class="tab" onclick="switchTab(event, 'tab3')">Tab 3</button>
</div>

<div id="tab1" class="tab-content active">
    Content for tab 1
</div>
<div id="tab2" class="tab-content">
    Content for tab 2
</div>
<div id="tab3" class="tab-content">
    Content for tab 3
</div>

<script>
function switchTab(event, tabId) {
    // Remove active from all tabs and content
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    // Add active to clicked tab and corresponding content
    event.target.classList.add('active');
    document.getElementById(tabId).classList.add('active');
}
</script>

<!-- Pill Tabs -->
<div class="tabs tabs-pills">
    <button class="tab active">Home</button>
    <button class="tab">Profile</button>
    <button class="tab">Settings</button>
</div>
```

### Progress Bars
```html
<div class="progress">
    <div class="progress-bar" style="width: 60%;"></div>
</div>

<!-- Colored Progress -->
<div class="progress">
    <div class="progress-bar progress-bar-success" style="width: 75%;"></div>
</div>

<!-- Striped Progress -->
<div class="progress">
    <div class="progress-bar progress-bar-striped" style="width: 50%;"></div>
</div>

<!-- Animated Progress -->
<div class="progress">
    <div class="progress-bar progress-bar-striped progress-bar-animated" style="width: 40%;"></div>
</div>
```

### Spinners / Loaders
```html
<!-- Spinner -->
<div class="spinner"></div>
<div class="spinner spinner-sm"></div>
<div class="spinner spinner-lg"></div>

<!-- Loading Overlay -->
<div class="loading-overlay">
    <div class="loading-content">
        <div class="spinner spinner-lg"></div>
        <div class="loading-text">Loading...</div>
    </div>
</div>
```

### Pagination
```html
<div class="pagination">
    <button class="pagination-item" disabled>«</button>
    <button class="pagination-item active">1</button>
    <button class="pagination-item">2</button>
    <button class="pagination-item">3</button>
    <button class="pagination-item">4</button>
    <button class="pagination-item">5</button>
    <button class="pagination-item">»</button>
</div>
```

### Avatars
```html
<!-- Text Avatar -->
<div class="avatar avatar-md">JD</div>

<!-- Image Avatar -->
<div class="avatar avatar-md">
    <img src="profile.jpg" alt="User">
</div>

<!-- Avatar Sizes -->
<div class="avatar avatar-xs">XS</div>
<div class="avatar avatar-sm">SM</div>
<div class="avatar avatar-md">MD</div>
<div class="avatar avatar-lg">LG</div>
<div class="avatar avatar-xl">XL</div>

<!-- Avatar Group -->
<div class="avatar-group">
    <div class="avatar avatar-sm">JD</div>
    <div class="avatar avatar-sm">AB</div>
    <div class="avatar avatar-sm">CD</div>
    <div class="avatar avatar-sm">+5</div>
</div>
```

### Breadcrumbs
```html
<nav class="breadcrumb">
    <div class="breadcrumb-item"><a href="#">Home</a></div>
    <span class="breadcrumb-separator">/</span>
    <div class="breadcrumb-item"><a href="#">Products</a></div>
    <span class="breadcrumb-separator">/</span>
    <div class="breadcrumb-item active">Details</div>
</nav>
```

## Grid System

### Container
```html
<div class="container">
    <!-- Max-width centered content -->
</div>

<div class="container-fluid">
    <!-- Full-width content -->
</div>
```

### Grid Layouts
```html
<!-- Auto Grid -->
<div class="grid grid-two">
    <div>Column 1</div>
    <div>Column 2</div>
</div>

<div class="grid grid-three">
    <div>Column 1</div>
    <div>Column 2</div>
    <div>Column 3</div>
</div>

<div class="grid grid-four">
    <div>Column 1</div>
    <div>Column 2</div>
    <div>Column 3</div>
    <div>Column 4</div>
</div>

<!-- Stats Grid (auto-fit) -->
<div class="stats-grid">
    <div class="stats-card">...</div>
    <div class="stats-card">...</div>
    <div class="stats-card">...</div>
</div>

<!-- Content Grid (2/3 + 1/3) -->
<div class="content-grid">
    <div>Main Content</div>
    <div>Sidebar</div>
</div>

<!-- Flexbox Row -->
<div class="row">
    <div class="col">Column 1</div>
    <div class="col">Column 2</div>
    <div class="col">Column 3</div>
</div>
```

## Customization

### CSS Variables
All colors, sizes, and spacing can be customized via CSS custom properties:

```css
:root {
    /* Brand Colors */
    --brand-primary: #4b49ac;
    --brand-primary-hover: #3f3d91;
    --brand-secondary: #7978e9;
    
    /* Status Colors */
    --color-success: #10b981;
    --color-warning: #f59e0b;
    --color-danger: #ef4444;
    --color-info: #3b82f6;
    
    /* Spacing */
    --spacing-4: 1rem;
    --spacing-6: 1.5rem;
    
    /* Typography */
    --font-size-base: 1rem;
    --font-weight-medium: 500;
    
    /* Border Radius */
    --radius-md: 8px;
    --radius-lg: 12px;
}
```

### Dark Mode Override
```css
[data-theme="dark"] {
    --bg-body: #0b1120;
    --bg-card: #1f2937;
    --text-primary: #f9fafb;
    /* ... other dark theme variables */
}
```

## Utility Classes

### Display
- `d-none`, `d-block`, `d-inline`, `d-flex`, `d-grid`, `hidden`

### Flexbox
- `flex`, `flex-col`, `flex-row`, `flex-wrap`
- `items-center`, `items-start`, `items-end`
- `justify-center`, `justify-between`, `justify-end`
- `gap-2`, `gap-4`, `gap-6`, `gap-8`

### Text
- `text-left`, `text-center`, `text-right`
- `text-primary`, `text-secondary`, `text-muted`
- `text-success`, `text-warning`, `text-danger`
- `text-xs`, `text-sm`, `text-base`, `text-lg`, `text-xl`
- `font-normal`, `font-medium`, `font-semibold`, `font-bold`

### Spacing
- Margin: `m-0`, `mt-4`, `mb-4`, `ml-auto`, `mr-auto`
- Padding: `p-0`, `p-4`, `pt-4`, `pb-4`

### Width
- `w-auto`, `w-full`, `w-50`

### Misc
- `rounded`, `rounded-lg`, `rounded-full`
- `shadow-sm`, `shadow`, `shadow-lg`
- `cursor-pointer`, `opacity-50`

## Browser Support
- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Modern mobile browsers

## Migration Notes

### From Your Current Styles
1. Replace old CSS variables with new theme variables
2. Update class names to match new conventions
3. Remove legacy Bootstrap dependencies
4. Test dark mode implementation
5. Update JavaScript for theme toggling

### Breaking Changes
- Button classes now use `btn-` prefix consistently
- Form elements use `form-` prefix
- Table classes use `table-` prefix
- All spacing utilities follow consistent naming

## License
Free to use in your projects. No attribution required.

## Support
For issues or questions, refer to the CSS source code which is heavily commented.
