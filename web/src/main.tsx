import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { FilterProvider } from './components/Filter';
import { TooltipProvider } from './components/Tooltip';
import '@fontsource-variable/geist';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <FilterProvider>
      <TooltipProvider>
        <App />
      </TooltipProvider>
    </FilterProvider>
  </StrictMode>,
);
