import Navbar from "../../components/marketing/Navbar";
import Hero from "../../components/marketing/Hero";
import TrustStrip from "../../components/marketing/TrustStrip";
import Features from "../../components/marketing/Features";
import HowItWorks from "../../components/marketing/HowItWorks";
import Integration from "../../components/marketing/Integration";
import Security from "../../components/marketing/Security";
import Pricing from "../../components/marketing/Pricing";
import FAQ from "../../components/marketing/FAQ";
import { CTA, Footer } from "../../components/marketing/CTAFooter";

export default function Landing() {
  return (
    <div className="bg-surface">
      <Navbar />
      <Hero />
      <TrustStrip />
      <Features />
      <HowItWorks />
      <Integration />
      <Security />
      <Pricing />
      <FAQ />
      <CTA />
      <Footer />
    </div>
  );
}
