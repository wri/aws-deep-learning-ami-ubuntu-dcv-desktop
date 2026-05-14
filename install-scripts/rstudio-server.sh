# https://posit.co/download/rstudio-server/

# Install r-base https://cran.rstudio.com/bin/linux/ubuntu/
# update indices
sudo apt update -qq
# install two helper packages we need
sudo apt install --no-install-recommends software-properties-common dirmngr -y
# add the signing key (by Michael Rutter) for these repos
# To verify key, run gpg --show-keys /etc/apt/trusted.gpg.d/cran_ubuntu_key.asc 
# Fingerprint: E298A3A825C0D65DFD57CBB651716619E084DAB9
wget -qO- https://cloud.r-project.org/bin/linux/ubuntu/marutter_pubkey.asc | sudo tee -a /etc/apt/trusted.gpg.d/cran_ubuntu_key.asc
# add the repo from CRAN -- lsb_release adjusts to 'noble' or 'jammy' or ... as needed
sudo add-apt-repository "deb https://cloud.r-project.org/bin/linux/ubuntu $(lsb_release -cs)-cran40/" -y
# install R itself
sudo apt install --no-install-recommends r-base -y


# Install RStduio Server
sudo apt-get install gdebi-core -y
cd Downloads
wget https://download2.rstudio.org/server/jammy/amd64/rstudio-server-2025.09.2-418-amd64.deb
sudo gdebi rstudio-server-2025.09.2-418-amd64.deb -n
