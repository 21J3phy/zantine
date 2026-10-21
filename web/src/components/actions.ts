import { createContext, useContext } from 'react';

export interface Actions {
  openFigure: (id: string) => void;
  openPaper: (slug: string) => void;
  goTo: (section: string) => void;
}

export const ActionsContext = createContext<Actions>({ openFigure: () => {}, openPaper: () => {}, goTo: () => {} });

export const useActions = () => useContext(ActionsContext);
