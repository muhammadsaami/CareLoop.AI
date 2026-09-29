import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App.tsx'
import './styles/index.css'

const container = document.getElementById('root')

// A missing #root means the deployed HTML and this bundle disagree, which is
// worth an obvious message rather than a blank page.
if (!container) {
  throw new Error('CareLoop could not start: the #root element is missing from the page.')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
