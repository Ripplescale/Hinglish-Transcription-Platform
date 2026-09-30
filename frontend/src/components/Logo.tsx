import React from 'react';
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from './ui/dialog';
import { VisuallyHidden } from './ui/visually-hidden';
import { About } from './About';
import { Brand } from './Brand';

const Logo = React.forwardRef<HTMLButtonElement, { isCollapsed: boolean }>(({ isCollapsed }, ref) => <Dialog>
  <DialogTrigger asChild><button ref={ref} type="button" className="rounded-lg text-left transition-opacity hover:opacity-75" aria-label="About xx"><Brand compact={isCollapsed} tagline={!isCollapsed} /></button></DialogTrigger>
  <DialogContent className="max-w-md"><VisuallyHidden><DialogTitle>About xx</DialogTitle></VisuallyHidden><About /></DialogContent>
</Dialog>);
Logo.displayName = 'Logo';
export default Logo;
